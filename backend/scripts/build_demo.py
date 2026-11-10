"""Build (or check) the precomputed demo fixture: alert templates for one replay cycle.

    python scripts/build_demo.py          # write data/demo_fixture.json
    python scripts/build_demo.py --check  # exit 1 if the committed fixture is out of date (CI)

The build is deterministic: it runs the tracker and every detection over the concatenated
recordings with the scheduled injected scenarios, and writes the resulting alerts as offsets
within the cycle.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghosttrack import config, demo  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if the committed fixture differs from a fresh build")
    args = ap.parse_args(argv)
    fresh = demo.fixture_json(demo.build_fixture())
    path = config.DEMO_FIXTURE
    if args.check:
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != fresh:
            print(f"{path} is out of date: run `python scripts/build_demo.py` and commit the result", file=sys.stderr)
            return 1
        print(f"{path.name} is current")
        return 0
    path.write_text(fresh, encoding="utf-8", newline="\n")
    print(f"wrote {path} ({fresh.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
