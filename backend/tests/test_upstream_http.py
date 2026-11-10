"""Exercise the REAL opener path against a local 127.0.0.1 HTTP server (no external network)."""
import gzip
import http.server
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from ghosttrack import upstream
from ghosttrack.upstream import UpstreamError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import record as recmod  # noqa: E402

GOOD = json.dumps({"ac": [{"hex": "a1b2c3", "lat": 33.9, "lon": -118.4}], "now": 1794299503501}).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    hits: list = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.hits.append(self.path)
        m = Handler.mode
        if m == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/evil")
            self.end_headers()
        elif m == "429":
            self.send_response(429)
            self.send_header("Retry-After", "7")
            self.end_headers()
        elif m == "429-huge":
            self.send_response(429)
            self.send_header("Retry-After", "99999")
            self.end_headers()
        elif m == "503":
            self.send_response(503)
            self.end_headers()
        elif m == "drip":
            self.send_response(200)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(GOOD)))
            self.end_headers()
            self.wfile.write(GOOD)


@pytest.fixture()
def server():
    Handler.hits, Handler.mode = [], "ok"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_real_opener_happy_path(server):
    snap, stats = upstream.fetch_snapshot("socal", _base_url=server)
    assert len(snap.aircraft) == 1 and Handler.hits == ["/v2/point/33.9400/-118.4100/60"]


def test_redirect_refused_and_not_followed(server):
    Handler.mode = "redirect"
    with pytest.raises(UpstreamError) as ei:
        upstream.fetch_snapshot("socal", _base_url=server)
    assert ei.value.status == 302 and "redirect refused" in str(ei.value)
    assert len(Handler.hits) == 1  # the Location target was never contacted


def test_429_surfaces_status_and_retry_after(server):
    Handler.mode = "429"
    with pytest.raises(UpstreamError) as ei:
        upstream.fetch_snapshot("socal", _base_url=server)
    assert ei.value.status == 429 and ei.value.retry_after == 7.0


def test_retry_after_is_capped(server):
    Handler.mode = "429-huge"
    with pytest.raises(UpstreamError) as ei:
        upstream.fetch_snapshot("socal", _base_url=server)
    assert ei.value.retry_after == upstream.MAX_RETRY_AFTER_S


def test_5xx_surfaces_status(server):
    Handler.mode = "503"
    with pytest.raises(UpstreamError) as ei:
        upstream.fetch_snapshot("socal", _base_url=server)
    assert ei.value.status == 503 and ei.value.retry_after is None


def test_slow_drip_hits_total_deadline(server, monkeypatch):
    Handler.mode = "drip"
    monkeypatch.setattr(upstream, "TOTAL_DEADLINE_S", 0.6)
    t0 = time.monotonic()
    with pytest.raises(UpstreamError, match="timeout"):
        upstream.fetch_snapshot("socal", _base_url=server)
    assert time.monotonic() - t0 < 3.0  # well under the per-socket 3 s * data volume


def test_env_proxy_is_ignored(server, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    assert len(upstream.fetch_snapshot("socal", _base_url=server)[0].aircraft) == 1


def test_production_opener_has_no_http_or_redirect_handlers():
    op = upstream.make_opener()
    names = {type(h).__name__ for h in op.handlers}
    assert "HTTPHandler" not in names and "HTTPSHandler" in names
    assert "_NoRedirect" in names and "HTTPRedirectHandler" not in names
    # an empty ProxyHandler registers no proxy_open methods, so it does not appear; no proxy handler may
    assert "ProxyHandler" not in names and "FileHandler" not in names and "FTPHandler" not in names
    assert not op.handle_open.get("http") and not any("proxy" in k for k in op.handle_open)


def test_production_url_cannot_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_UPSTREAM", "http://evil.test")
    assert upstream.build_url("socal").startswith("https://api.adsb.lol/")


def test_deadline_with_fake_response():
    class Drip:
        headers = {}

        def read(self, n):
            time.sleep(0.05)
            return b"x"

    with pytest.raises(UpstreamError, match="timeout"):
        upstream._read_capped(Drip(), deadline_s=0.2)


# --- recorder behaviour --------------------------------------------------------

def test_next_delay_honours_retry_after():
    e429 = UpstreamError("HTTP 429", status=429, retry_after=45)
    assert recmod.next_delay(10, 10, e429) == 45
    assert recmod.next_delay(10, 10, UpstreamError("HTTP 503", status=503)) == 20
    assert recmod.next_delay(50, 10, UpstreamError("x")) == recmod.MAX_BACKOFF_S
    assert recmod.DEFAULT_INTERVAL_S == 10.0 and recmod.MIN_INTERVAL_S == 10.0


def test_recorder_enforces_ten_second_minimum_poll(tmp_path):
    with pytest.raises(ValueError, match="10"):
        recmod.record("fast", 1, "socal", 5, rec_dir=tmp_path, fetch=lambda *a, **k: None, sleep=lambda s: None)
    assert not list(tmp_path.iterdir())


def test_record_refuses_overwrite_without_force(tmp_path):
    (tmp_path / "x.jsonl.gz").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        recmod.record("x", 1, "socal", 10, rec_dir=tmp_path)
    assert (tmp_path / "x.jsonl.gz").read_bytes() == b"keep"


def test_record_cleans_leftover_tmp_and_on_interrupt(tmp_path):
    (tmp_path / "y.jsonl.tmp").write_bytes(b"stale")

    def boom(region):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        recmod.record("y", 1, "socal", 10, rec_dir=tmp_path, fetch=boom, sleep=lambda s: None)
    assert list(tmp_path.iterdir()) == []


def test_record_honours_retry_after_and_writes(tmp_path):
    snap = upstream.parse_response(GOOD)[0]
    calls, sleeps = [], []

    def fetch(region):
        calls.append(1)
        if len(calls) == 1:
            raise UpstreamError("HTTP 429", status=429, retry_after=30)
        return snap, {"records_kept": 1}

    out = recmod.record("z", 0.02, "socal", 10, rec_dir=tmp_path, fetch=fetch, sleep=lambda s: (sleeps.append(s), time.sleep(0.4)))
    assert sleeps[0] >= 29  # first sleep respects Retry-After (minus elapsed)
    assert out.exists() and not (tmp_path / "z.jsonl.tmp").exists()
    with gzip.open(out, "rt") as f:
        assert len(f.readlines()) == 1
