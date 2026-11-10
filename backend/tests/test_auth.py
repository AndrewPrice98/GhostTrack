"""Authentication, ported from CMMC-Ready. The routes below mirror the ones the API exposes,
so the tests exercise the real auth module through a small FastAPI app."""

from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from ghosttrack import auth, config
from conftest import TEST_PASSWORD, TEST_USER


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "auth_configured": auth.is_configured(),
                "demo": config.demo_mode(), "guest": config.guest_enabled()}

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response):
        if not auth.is_configured():
            raise HTTPException(503, "Authentication is not configured. Run: python -m ghosttrack create-admin")
        ip = auth.client_ip(request)
        wait = auth.limiter.retry_after(ip)
        if wait:
            raise HTTPException(429, "Too many failed logins. Try again later.",
                                headers={"Retry-After": str(wait)})
        if not auth.check_credentials(body.username, body.password):
            auth.limiter.record_failure(ip)
            raise HTTPException(401, "Invalid username or password")
        auth.limiter.reset(ip)
        token, expires = auth.issue_token(body.username)
        response.set_cookie(auth.COOKIE_NAME, token, expires=expires, httponly=True,
                            secure=config.cookie_secure(), samesite="strict", path="/api")
        return {"username": body.username}

    @app.post("/api/auth/logout")
    def logout(response: Response):
        response.delete_cookie(auth.COOKIE_NAME, path="/api", httponly=True,
                               secure=config.cookie_secure(), samesite="strict")
        return {"ok": True}

    protected = APIRouter(dependencies=[Depends(auth.require_user)])

    @protected.get("/api/auth/me")
    def me(user: Annotated[str, Depends(auth.require_user)]):
        return {"username": user, "guest": user == auth.GUEST_USER}

    @protected.get("/api/alerts")
    def read_something():
        return []

    @protected.post("/api/alerts")
    def write_something(_user: Annotated[str, Depends(auth.require_writer)]):
        return {"ok": True}

    app.include_router(protected)
    return app


app = make_app()

PROTECTED = [
    ("get", "/api/auth/me"),
    ("get", "/api/alerts"),
    ("post", "/api/alerts"),
]


@pytest.fixture
def anon():
    return TestClient(app)


def login(client, username=TEST_USER, password=TEST_PASSWORD, **kw):
    return client.post("/api/auth/login", json={"username": username, "password": password}, **kw)


# -- password hashing ------------------------------------------------------------------------

def test_default_iterations_is_owasp_value():
    assert auth.DEFAULT_ITERATIONS == 600_000


def test_password_hash_roundtrip_and_salting():
    h1, h2 = auth.hash_password("s3cret-pass", 1000), auth.hash_password("s3cret-pass", 1000)
    assert h1 != h2  # random salt
    assert auth.verify_password("s3cret-pass", h1)
    assert not auth.verify_password("wrong", h1)
    assert "s3cret-pass" not in h1


@pytest.mark.parametrize("bad", ["", "plaintext", "md5:1:a:b", "pbkdf2_sha256:x:y:z"])
def test_malformed_hashes_never_verify(bad):
    assert not auth.verify_password("anything", bad)


# -- access control ----------------------------------------------------------------------------

@pytest.mark.parametrize("method, path", PROTECTED)
def test_protected_endpoints_require_login(anon, method, path):
    r = getattr(anon, method)(path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_health_is_public_and_reports_auth_state(anon):
    body = anon.get("/api/health").json()
    assert body["status"] == "ok" and body["auth_configured"] is True


def test_login_sets_hardened_cookie_and_grants_access(anon):
    r = login(anon)
    assert r.status_code == 200 and r.json()["username"] == TEST_USER
    cookie = r.headers["set-cookie"].lower()
    assert "ghosttrack_session=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api" in cookie
    assert anon.get("/api/auth/me").json() == {"username": TEST_USER, "guest": False}
    assert anon.get("/api/alerts").status_code == 200


def test_cookie_is_secure_by_default(anon, monkeypatch):
    monkeypatch.delenv("GHOSTTRACK_COOKIE_SECURE")
    assert "secure" in login(anon).headers["set-cookie"].lower()


def test_bearer_token_works_without_cookie(anon):
    client = TestClient(app)
    response = login(client)
    assert "token" not in response.json()
    token = client.cookies.get(auth.COOKIE_NAME)
    assert token
    r = anon.get("/api/alerts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


@pytest.mark.parametrize("username, password", [
    (TEST_USER, "wrong password"),
    ("root", TEST_PASSWORD),
    ("ADMIN", TEST_PASSWORD),
])
def test_bad_credentials_are_rejected_with_one_message(anon, username, password):
    r = login(anon, username, password)
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password"
    assert "set-cookie" not in r.headers


def test_login_validates_input(anon):
    assert anon.post("/api/auth/login", json={"username": "", "password": "x"}).status_code == 422
    assert anon.post("/api/auth/login", json={"username": "a" * 500, "password": "x"}).status_code == 422


def test_logout_clears_cookie(anon):
    login(anon)
    anon.post("/api/auth/logout")
    assert anon.get("/api/auth/me").status_code == 401


# -- tokens ------------------------------------------------------------------------------------

def _token(secret="test-secret-" + "x" * 40, **claims):
    now = datetime.now(timezone.utc)
    payload = {"sub": TEST_USER, "iat": now, "exp": now + timedelta(hours=1), **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.parametrize("token", [
    _token(exp=datetime.now(timezone.utc) - timedelta(seconds=5)),  # expired
    _token(secret="a-different-secret-" + "y" * 40),  # forged signature
    _token(sub="someone-else"),  # not the admin
    "not.a.jwt",
    jwt.encode({"sub": TEST_USER, "exp": 9999999999, "iat": 0}, key=None, algorithm="none"),  # unsigned
])
def test_invalid_tokens_are_rejected(anon, token):
    assert anon.get("/api/alerts", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_rotating_secret_invalidates_sessions(anon, monkeypatch):
    login(anon)
    monkeypatch.setenv("GHOSTTRACK_SECRET_KEY", "rotated-secret-" + "z" * 40)
    assert anon.get("/api/auth/me").status_code == 401


# -- brute-force protection ----------------------------------------------------------------------

def test_failed_logins_are_rate_limited(anon):
    for _ in range(5):
        assert login(anon, password="nope").status_code == 401
    r = login(anon)  # even the right password is refused while locked out
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_successful_login_resets_failure_count(anon):
    for _ in range(4):
        login(anon, password="nope")
    assert login(anon).status_code == 200
    for _ in range(4):
        login(anon, password="nope")
    assert login(anon).status_code == 200


def test_forwarded_ip_only_trusted_when_configured(anon, monkeypatch):
    spoof = {"X-Forwarded-For": "1.2.3.4"}
    for i in range(5):
        login(anon, password="nope", headers={"X-Forwarded-For": f"9.9.9.{i}"})
    # Not trusting the header: all five failures counted against the real client.
    assert login(anon, headers=spoof).status_code == 429
    auth.limiter.reset()
    monkeypatch.setenv("GHOSTTRACK_TRUST_PROXY", "true")
    for _ in range(5):
        login(anon, password="nope", headers=spoof)
    assert login(anon, headers=spoof).status_code == 429
    assert login(anon, headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200


# -- fail closed ----------------------------------------------------------------------------------

@pytest.mark.parametrize("var, value", [
    ("GHOSTTRACK_ADMIN_PASSWORD_HASH", ""),
    ("GHOSTTRACK_SECRET_KEY", ""),
    ("GHOSTTRACK_SECRET_KEY", "too-short"),
])
def test_unconfigured_auth_fails_closed(anon, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    assert login(anon).status_code == 503
    assert anon.get("/api/alerts").status_code == 503
    assert anon.get("/api/health").json()["auth_configured"] is False


# -- guests (demo mode) ----------------------------------------------------------------------------

@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")


def test_guest_reads_without_login(demo_env, anon):
    assert anon.get("/api/auth/me").json() == {"username": "guest", "guest": True}
    assert anon.get("/api/alerts").status_code == 200


def test_guest_works_even_without_admin_configured(demo_env, anon, monkeypatch):
    monkeypatch.delenv("GHOSTTRACK_ADMIN_PASSWORD_HASH")
    assert anon.get("/api/alerts").status_code == 200


def test_guest_cannot_write(demo_env, anon):
    assert anon.post("/api/alerts").status_code == 403


def test_admin_still_writes_in_demo(demo_env, anon):
    login(anon)
    assert anon.get("/api/auth/me").json() == {"username": "admin", "guest": False}
    assert anon.post("/api/alerts").status_code == 200


def test_guest_can_be_disabled(demo_env, anon, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_GUEST", "false")
    assert anon.get("/api/alerts").status_code == 401


def test_health_reports_guest(demo_env, anon):
    assert anon.get("/api/health").json()["guest"] is True


def test_no_guest_outside_demo(anon):
    assert anon.get("/api/alerts").status_code == 401
    assert anon.get("/api/health").json()["guest"] is False


def test_unknown_user_still_pays_for_a_full_password_hash(monkeypatch):
    assert "_DUMMY_HASH" not in vars(auth)  # nothing is hashed at import time
    calls = []
    real = auth.hashlib.pbkdf2_hmac

    def spy(name, password, salt, iterations, *args):
        calls.append(iterations)
        return real(name, password, salt, iterations, *args)

    monkeypatch.setattr(auth.hashlib, "pbkdf2_hmac", spy)
    auth._dummy_hash.cache_clear()
    assert auth.check_credentials("nobody", "whatever") is False
    assert calls.count(auth.DEFAULT_ITERATIONS) == 2  # building the dummy, then verifying it
    calls.clear()
    assert auth.check_credentials("nobody", "whatever") is False
    assert calls == [auth.DEFAULT_ITERATIONS]  # cached: one full verify per attempt
