"""Single-admin authentication (ported from CMMC-Ready, same security properties).

- The password is stored only as a salted PBKDF2-SHA256 hash (GHOSTTRACK_ADMIN_PASSWORD_HASH).
- A successful login returns a signed JWT in an httpOnly, SameSite=Strict cookie, so page
  JavaScript never sees it and other sites cannot send it. The login response body does
  not contain the token. API clients keep the session cookie, or send the same token
  (the cookie's value) as "Authorization: Bearer <token>".
- Failed logins are rate-limited per client IP.
- Fail closed: if the admin or the signing secret is not configured, nothing is accessible.

Create the credentials with:  python -m ghosttrack create-admin
"""

import base64
import functools
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request

from . import config

COOKIE_NAME = "ghosttrack_session"
ALGORITHM = "HS256"
GUEST_USER = "guest"
HASH_SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000  # OWASP 2023+ recommendation for PBKDF2-SHA256

# -- password hashing -----------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Return 'pbkdf2_sha256:<iterations>:<salt>:<hash>'. Uses ':' so the value is safe in
    shells and .env files (no '$' expansion)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{HASH_SCHEME}:{iterations}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt, expected = encoded.split(":")
        if scheme != HASH_SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError):
        return False


# A real hash to verify against when the username is wrong, so a wrong username takes
# as long as a wrong password and response times don't reveal valid usernames.
# Computed on first use, not at import, so importing the module (tests, the CLI) stays fast.
@functools.cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16), iterations=DEFAULT_ITERATIONS)


def check_credentials(username: str, password: str) -> bool:
    expected_user = config.admin_user()
    expected_hash = config.admin_password_hash()
    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    pw_ok = verify_password(password, expected_hash if user_ok else _dummy_hash())
    return user_ok and pw_ok


MIN_SECRET_LENGTH = 32


def is_configured() -> bool:
    """True only with a password hash and a signing secret long enough to resist guessing."""
    return bool(config.admin_password_hash()) and len(config.secret_key()) >= MIN_SECRET_LENGTH


# -- tokens ------------------------------------------------------------------------------


def issue_token(username: str) -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(hours=config.session_hours())
    token = jwt.encode(
        {"sub": username, "exp": expires, "iat": datetime.now(timezone.utc)},
        config.secret_key(),
        algorithm=ALGORITHM,
    )
    return token, expires


def decode_token(token: str) -> str | None:
    """Return the username for a valid token, else None."""
    try:
        # Pin the algorithm: never trust the token's own "alg" header.
        claims = jwt.decode(token, config.secret_key(), algorithms=[ALGORITHM],
                            options={"require": ["sub", "exp", "iat"]})
    except jwt.PyJWTError:
        return None
    sub = claims.get("sub")
    # A token for a renamed/removed admin is no longer valid.
    return sub if sub == config.admin_user() else None


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE_NAME)


def require_user(request: Request) -> str:
    """FastAPI dependency: the logged-in username, the read-only guest (when enabled), or 401."""
    token = _token_from_request(request) if is_configured() else None
    user = decode_token(token) if token else None
    if user is not None:
        return user
    if config.guest_enabled():
        return GUEST_USER
    if not is_configured():
        raise HTTPException(503, "Authentication is not configured. Run: python -m ghosttrack create-admin")
    raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def require_writer(user: str = Depends(require_user)) -> str:
    """FastAPI dependency for endpoints that change data: guests are refused."""
    if user == GUEST_USER:
        raise HTTPException(403, "Guests have read-only access. Log in to do this.")
    return user


# -- login rate limiting --------------------------------------------------------------------


class LoginLimiter:
    """At most `max_failures` failed logins per client IP per `window` seconds.

    In-memory, so the limit is per server process. Behind several instances (e.g. serverless),
    each instance counts separately; a shared store such as Redis would be needed to make the
    limit global.
    """

    def __init__(self, max_failures: int = 5, window: float = 300.0):
        self.max_failures, self.window = max_failures, window
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._failures[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until this client may try again (0 if allowed now)."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) < self.max_failures:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(time.monotonic())

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


limiter = LoginLimiter()


def client_ip(request: Request) -> str:
    # Behind a trusted proxy (Vercel, Render, Fly...), the real client is the first
    # X-Forwarded-For entry. Only trust it when configured to, since clients can set it.
    if config.trust_proxy():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
