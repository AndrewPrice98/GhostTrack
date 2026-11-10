"""Command-line entry point: ``python -m ghosttrack validate`` (serve and data tooling come later)."""
from __future__ import annotations

import argparse
import sys

from ghosttrack.validate import budget_exceeded, format_report, measure_false_positives, run_validation


def cmd_validate() -> int:
    results = run_validation()
    fps = measure_false_positives()
    print(format_report(results, fps))
    failed = [r.scenario for r in results if not r.passed or r.collateral]
    over = budget_exceeded(fps)
    if failed:
        print(f"\nFAILED scenarios: {', '.join(failed)}", file=sys.stderr)
    if over:
        print(f"FALSE-POSITIVE BUDGET EXCEEDED: {', '.join(r.recording for r in over)}", file=sys.stderr)
    return 1 if failed or over else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ghosttrack")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="run the attack scenarios and measure false positives")
    args = ap.parse_args(argv)
    if args.command == "validate":
        return cmd_validate()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
