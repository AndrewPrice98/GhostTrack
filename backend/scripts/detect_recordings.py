"""Run every detection over the committed recordings and print alert counts and findings.

    python scripts/detect_recordings.py [--findings] [recording.jsonl.gz ...]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghosttrack.detections import ALL_IDS, analyse  # noqa: E402
from ghosttrack.model import Snapshot  # noqa: E402

REC_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"


def load(path: Path) -> list[Snapshot]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [Snapshot.from_dict(json.loads(line)) for line in fh if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--findings", action="store_true", help="print every finding")
    args = ap.parse_args(argv)
    paths = args.paths or sorted(REC_DIR.glob("*.jsonl.gz"))
    totals = dict.fromkeys(ALL_IDS, 0)
    for path in paths:
        an = analyse(load(path))
        counts = an.counts()
        for k, v in counts.items():
            totals[k] += v
        print(f"{path.name}: tracks={len(an.tracker.tracks)} " + " ".join(f"{k}={v}" for k, v in counts.items()))
        if args.findings:
            for f in an.findings:
                when = datetime.fromtimestamp(f.ts_first, timezone.utc).strftime("%H:%M:%SZ")
                print(f"  {f.detection_id} {f.icao} {when} conf={f.confidence} {f.explanation}")
    print("total: " + " ".join(f"{k}={v}" for k, v in totals.items()) + f" (all={sum(totals.values())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
