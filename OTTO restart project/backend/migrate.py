"""Explicit CLI for PostgreSQL schema changes; never invoked by API startup."""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from migration_runner import MigrationError, check_schema_head, downgrade, upgrade


def _database_url() -> str:
    value = os.environ.get("OTTORESTART_MIGRATION_DATABASE_URL", "").strip()
    if not value:
        raise MigrationError(
            "Set OTTORESTART_MIGRATION_DATABASE_URL explicitly; the API database URL is not used by this CLI."
        )
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise MigrationError("Migration CLI accepts PostgreSQL URLs only.")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explicit OttoRestart PostgreSQL migration commands")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("upgrade", help="Apply pending PostgreSQL migrations")
    commands.add_parser("check", help="Check current schema version without changing it")
    down = commands.add_parser("downgrade", help="Downgrade an empty schema using reviewed down scripts")
    down.add_argument("--target", type=int, required=True, help="Target schema version (0 removes all revisions)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    engine = None
    try:
        engine = create_engine(_database_url(), pool_pre_ping=True, echo=False)
        if args.command == "upgrade":
            report = upgrade(engine)
            applied = ", ".join(f"{version:04d}" for version in report.applied_versions) or "none"
            adopted = ", ".join(f"{version:04d}" for version in report.adopted_legacy_versions) or "none"
            print(
                f"Schema head {report.head_version:04d}; applied: {applied}; "
                f"legacy checksums adopted (not historically verified): {adopted}"
            )
        elif args.command == "check":
            status = check_schema_head(engine)
            print(f"Schema head {status.current_version:04d}/{status.expected_version:04d}; checksums valid.")
        else:
            versions = downgrade(engine, args.target)
            result = ", ".join(f"{version:04d}" for version in versions) or "none"
            print(f"Downgraded revisions: {result}")
        return 0
    except MigrationError as exc:
        print(f"Migration command refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # Database driver exceptions can include connection details. Keep the
        # CLI output free of URLs, credentials, and raw driver diagnostics.
        print("Migration command failed; details omitted to protect connection credentials.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
