"""Command-line entry point: validate, create-admin, serve and demo."""
from __future__ import annotations

import argparse
import sys

from ghosttrack.validate import (
    budget_exceeded,
    format_report,
    measure_false_positives,
    run_gap_validation,
    run_validation,
)


def cmd_validate() -> int:
    results = run_validation()
    fps = measure_false_positives()
    gaps = run_gap_validation()
    print(format_report(results, fps, gaps=gaps))
    failed = [r.scenario for r in [*results, *gaps] if not r.passed or r.collateral]
    over = budget_exceeded(fps)
    if failed:
        print(f"\nFAILED scenarios: {', '.join(failed)}", file=sys.stderr)
    if over:
        print(f"FALSE-POSITIVE BUDGET EXCEEDED: {', '.join(r.recording for r in over)}", file=sys.stderr)
    return 1 if failed or over else 0


CRLF = chr(13) + chr(10)


def cmd_create_admin(args: argparse.Namespace) -> int:
    """Print the environment variables for the admin login (nothing is written to disk)."""
    import getpass
    import secrets

    from ghosttrack import config
    from ghosttrack.auth import hash_password

    password = sys.stdin.readline().rstrip(CRLF) if args.password_stdin else getpass.getpass("Admin password: ")
    if len(password) < 12:
        print("Use a password of at least 12 characters.", file=sys.stderr)
        return 2
    print("Set these environment variables on your host. Keep them secret.")
    print()
    print(f"GHOSTTRACK_ADMIN_USER={args.username}")
    print(f"GHOSTTRACK_ADMIN_PASSWORD_HASH={hash_password(password)}")
    print(f"GHOSTTRACK_SECRET_KEY={config.secret_key() or secrets.token_urlsafe(48)}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    if args.command == "demo":  # hosted-demo behaviour, locally: replay, read-only guests
        for name, value in (("GHOSTTRACK_DEMO", "true"), ("GHOSTTRACK_COOKIE_SECURE", "false")):
            if not os.environ.get(name, "").strip():  # blank counts as unset
                os.environ[name] = value
    uvicorn.run("ghosttrack.api:app", host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ghosttrack")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="run the attack scenarios and measure false positives")
    admin = sub.add_parser("create-admin", help="print the admin login environment variables")
    admin.add_argument("--username", default="admin")
    admin.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    for name, text in (("serve", "run the API"), ("demo", "run the API in demo mode (replay, read-only guests)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    if args.command == "validate":
        return cmd_validate()
    if args.command == "create-admin":
        return cmd_create_admin(args)
    if args.command in ("serve", "demo"):
        return cmd_serve(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
