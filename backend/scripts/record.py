"""Record an adsb.lol region to backend/data/recordings/<name>.jsonl.gz.

Usage: python scripts/record.py --name socal-a --minutes 20 [--region socal] [--interval 5]
Polls sequentially (single-threaded), 3 s timeout, exponential back-off on errors.
Military-flagged / DoD-block aircraft are excluded before anything is written.
Data is from adsb.lol under the ODbL; see data/LICENSE-ODbL.md.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghosttrack.upstream import UpstreamError, fetch_snapshot  # noqa: E402

REC_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize(path: Path) -> dict:
    """Manifest entry computed from the file itself."""
    n, icaos, first, last = 0, set(), None, None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            snap = json.loads(line)
            n += 1
            first = snap["ts"] if first is None else first
            last = snap["ts"]
            icaos.update(a["icao"] for a in snap["aircraft"])
    return {
        "name": path.name,
        "start_utc": iso(first) if first else None,
        "end_utc": iso(last) if last else None,
        "snapshots": n,
        "aircraft": len(icaos),
        "sha256": sha256_file(path),
    }


def update_manifest(rec_dir: Path = REC_DIR) -> list[dict]:
    entries = [summarize(p) for p in sorted(rec_dir.glob("*.jsonl.gz"))]
    (rec_dir / "manifest.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return entries


def record(name: str, minutes: float, region: str, interval: float, rec_dir: Path = REC_DIR) -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    out = rec_dir / f"{name}.jsonl.gz"
    tmp = out.with_suffix(".tmp")
    deadline = time.monotonic() + minutes * 60
    delay, last_ts, ok, fail = interval, None, 0, 0
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        while time.monotonic() < deadline:
            t0 = time.monotonic()
            try:
                snap, stats = fetch_snapshot(region)
                delay = interval
                if snap.ts != last_ts:  # skip duplicate upstream frames
                    f.write(json.dumps(snap.to_dict(), separators=(",", ":")) + "\n")
                    last_ts = snap.ts
                    ok += 1
                    if ok % 12 == 0:
                        print(f"{iso(time.time())} {name} ok={ok} fail={fail} kept={stats['records_kept']}", flush=True)
            except UpstreamError as exc:
                fail += 1
                delay = min(delay * 2, 60)
                print(f"{iso(time.time())} {name} error: {exc}; backoff {delay}s", flush=True)
            time.sleep(max(0.0, delay - (time.monotonic() - t0)))
    tmp.replace(out)
    print(f"done {name}: {ok} snapshots, {fail} failures", flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name")
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--region", default="socal")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--manifest-only", action="store_true")
    a = ap.parse_args(argv)
    if a.manifest_only:
        update_manifest()
        return 0
    if not a.name or not a.name.replace("-", "").replace("_", "").isalnum():
        ap.error("--name required (alphanumeric, - and _)")
    if a.interval < 5:
        ap.error("interval must be >= 5 s")
    record(a.name, a.minutes, a.region, a.interval)
    update_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
