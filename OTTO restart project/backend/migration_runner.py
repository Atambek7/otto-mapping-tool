"""Explicit, PostgreSQL-only OttoRestart migration commands.

The API imports this module only to check the schema head during startup.
Schema changes are applied exclusively by ``migrate.py upgrade``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SQLITE_BASELINE = "0001_initial.sql"
POSTGRES_BASELINE = "0001_postgres_initial.sql"
MIGRATION_LOCK_KEY = 5_726_847_913_004_118
MIGRATION_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """A migration could not be applied or verified safely."""


class SchemaNotReady(MigrationError):
    """The database schema needs an explicit migration upgrade."""


class MigrationChecksumMismatch(MigrationError):
    """An applied migration no longer matches its recorded checksum."""


class UnsafeDowngrade(MigrationError):
    """A downgrade was refused because it could remove application data."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True)
class MigrationReport:
    applied_versions: tuple[int, ...]
    adopted_legacy_versions: tuple[int, ...]
    head_version: int


@dataclass(frozen=True)
class SchemaStatus:
    current_version: int
    expected_version: int


def checksum_sql(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> tuple[Migration, ...]:
    """Load ordered PostgreSQL revisions, excluding the historical SQLite file."""
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        if path.name == SQLITE_BASELINE:
            continue
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if not match:
            continue
        version = int(match.group("version"))
        if version == 1 and path.name != POSTGRES_BASELINE:
            raise MigrationError(f"PostgreSQL baseline must be named {POSTGRES_BASELINE}.")
        raw = path.read_bytes()
        sql = raw.decode("utf-8")
        found.append(
            Migration(
                version=version,
                name=path.name,
                path=path,
                sql=sql,
                checksum=hashlib.sha256(raw).hexdigest(),
            )
        )

    if not found or found[0].version != 1 or found[0].name != POSTGRES_BASELINE:
        raise MigrationError(f"Required PostgreSQL baseline {POSTGRES_BASELINE} is missing.")
    versions = [migration.version for migration in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError("PostgreSQL migration versions must be contiguous starting at 0001.")
    return tuple(found)


def _require_postgresql(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise MigrationError("OttoRestart migrations support PostgreSQL only.")


def _acquire_lock(connection: Connection) -> None:
    connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": MIGRATION_LOCK_KEY})
    # SQLAlchemy autobegins on SELECT. End that transaction while retaining
    # the session-level advisory lock for this connection.
    connection.commit()


def _release_lock(connection: Connection) -> None:
    if connection.closed or connection.invalidated:
        return
    connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": MIGRATION_LOCK_KEY})
    connection.commit()


def _locked_connection(engine: Engine):
    """Context manager that owns a session-level advisory lock."""
    class LockedConnection:
        def __enter__(self) -> Connection:
            self.connection = engine.connect()
            try:
                _acquire_lock(self.connection)
            except Exception:
                self.connection.close()
                raise
            return self.connection

        def __exit__(self, exc_type, exc, traceback) -> bool:
            try:
                if self.connection.in_transaction():
                    self.connection.rollback()
                _release_lock(self.connection)
            finally:
                self.connection.close()
            return False

    return LockedConnection()


def _table_exists(connection: Connection) -> bool:
    return connection.execute(
        text("SELECT to_regclass('public.schema_migrations')")
    ).scalar_one() is not None


def _metadata_columns(connection: Connection) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'schema_migrations'"
            )
        ).scalars()
    )


def _bootstrap_metadata(connection: Connection) -> None:
    """Create the metadata table or extend the pre-checksum legacy table."""
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            migration_name TEXT,
            checksum_sha256 TEXT,
            checksum_source TEXT
        )
        """
    )
    connection.exec_driver_sql(
        "ALTER TABLE public.schema_migrations ADD COLUMN IF NOT EXISTS migration_name TEXT"
    )
    connection.exec_driver_sql(
        "ALTER TABLE public.schema_migrations ADD COLUMN IF NOT EXISTS checksum_sha256 TEXT"
    )
    connection.exec_driver_sql(
        "ALTER TABLE public.schema_migrations ADD COLUMN IF NOT EXISTS checksum_source TEXT"
    )


def _read_history(connection: Connection) -> dict[int, dict[str, object]]:
    if not _table_exists(connection):
        return {}
    required = {"version", "migration_name", "checksum_sha256", "checksum_source"}
    missing = required - _metadata_columns(connection)
    if missing:
        raise SchemaNotReady("Migration metadata is legacy; run the explicit upgrade command.")
    rows = connection.execute(
        text(
            "SELECT version, migration_name, checksum_sha256, checksum_source "
            "FROM public.schema_migrations ORDER BY version"
        )
    ).mappings()
    return {int(row["version"]): dict(row) for row in rows}


def _validate_version_set(applied: dict[int, dict[str, object]], migrations: Sequence[Migration]) -> None:
    expected = {migration.version for migration in migrations}
    unexpected = sorted(set(applied) - expected)
    if unexpected:
        raise SchemaNotReady(f"Database has migration versions unknown to this code: {unexpected}.")
    versions = sorted(applied)
    if versions and versions != list(range(1, versions[-1] + 1)):
        raise SchemaNotReady("Database migration history has a version gap; manual review is required.")


def _validate_record(row: dict[str, object], migration: Migration) -> None:
    recorded_name = row["migration_name"]
    recorded_checksum = row["checksum_sha256"]
    recorded_source = row["checksum_source"]
    if not recorded_name and not recorded_checksum and not recorded_source:
        return
    if not recorded_name or not recorded_checksum or not recorded_source:
        raise SchemaNotReady(
            f"Migration {migration.version:04d} has incomplete checksum metadata; manual review is required."
        )
    if recorded_name != migration.name or recorded_checksum != migration.checksum:
        raise MigrationChecksumMismatch(
            f"Applied migration {migration.version:04d} does not match its recorded name/checksum."
        )


def upgrade(engine: Engine, migrations: Sequence[Migration] | None = None) -> MigrationReport:
    """Apply pending PostgreSQL revisions serially, each in its own transaction."""
    _require_postgresql(engine)
    revisions = tuple(migrations or discover_migrations())
    _validate_revision_sequence(revisions)
    applied_now: list[int] = []
    adopted: list[int] = []

    with _locked_connection(engine) as connection:
        with connection.begin():
            _bootstrap_metadata(connection)
        with connection.begin():
            history = _read_history(connection)
            _validate_version_set(history, revisions)

        by_version = {migration.version: migration for migration in revisions}
        for version, row in sorted(history.items()):
            migration = by_version[version]
            if not row["migration_name"] and not row["checksum_sha256"] and not row["checksum_source"]:
                with connection.begin():
                    connection.execute(
                        text(
                            "UPDATE public.schema_migrations SET migration_name = :name, "
                            "checksum_sha256 = :checksum, checksum_source = 'legacy_adopted' "
                            "WHERE version = :version AND migration_name IS NULL "
                            "AND checksum_sha256 IS NULL AND checksum_source IS NULL"
                        ),
                        {"name": migration.name, "checksum": migration.checksum, "version": version},
                    )
                adopted.append(version)
            else:
                _validate_record(row, migration)

        last_version = max(history, default=0)
        for migration in revisions:
            if migration.version <= last_version:
                continue
            if migration.version != last_version + 1:
                raise SchemaNotReady("Cannot apply a migration while an earlier version is missing.")
            with connection.begin():
                connection.exec_driver_sql(migration.sql)
                connection.execute(
                    text(
                        "INSERT INTO public.schema_migrations "
                        "(version, applied_at, migration_name, checksum_sha256, checksum_source) "
                        "VALUES (:version, CURRENT_TIMESTAMP, :name, :checksum, 'verified')"
                    ),
                    {
                        "version": migration.version,
                        "name": migration.name,
                        "checksum": migration.checksum,
                    },
                )
            applied_now.append(migration.version)
            last_version = migration.version

        return MigrationReport(
            applied_versions=tuple(applied_now),
            adopted_legacy_versions=tuple(adopted),
            head_version=last_version,
        )


def _validate_revision_sequence(migrations: Sequence[Migration]) -> None:
    if not migrations or migrations[0].version != 1 or migrations[0].name != POSTGRES_BASELINE:
        raise MigrationError(f"Migration sequence must begin with {POSTGRES_BASELINE}.")
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError("Migration versions must be contiguous and ordered starting at 0001.")


def check_schema_head(
    engine: Engine, migrations: Sequence[Migration] | None = None
) -> SchemaStatus:
    """Verify metadata only; this function never creates or changes schema."""
    _require_postgresql(engine)
    revisions = tuple(migrations or discover_migrations())
    _validate_revision_sequence(revisions)
    with engine.connect() as connection:
        if not _table_exists(connection):
            raise SchemaNotReady("Database has no migration history; run the explicit upgrade command.")
        history = _read_history(connection)

    _validate_version_set(history, revisions)
    for migration in revisions:
        row = history.get(migration.version)
        if row is None:
            raise SchemaNotReady(
                f"Database schema is at version {max(history, default=0)}; expected {revisions[-1].version}."
            )
        if not row["migration_name"] or not row["checksum_sha256"] or not row["checksum_source"]:
            raise SchemaNotReady(
                f"Migration {migration.version:04d} has no verified migration metadata; run the explicit upgrade command."
            )
        if row["migration_name"] != migration.name or row["checksum_sha256"] != migration.checksum:
            raise MigrationChecksumMismatch(
                f"Applied migration {migration.version:04d} does not match its recorded name/checksum."
            )

    current = max(history, default=0)
    return SchemaStatus(current_version=current, expected_version=revisions[-1].version)


def _has_application_data(connection: Connection) -> bool:
    tables = connection.execute(
        text(
            "SELECT tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
        )
    ).scalars()
    quote = connection.dialect.identifier_preparer.quote
    for table in tables:
        statement = f"SELECT EXISTS (SELECT 1 FROM public.{quote(table)} LIMIT 1)"
        if connection.execute(text(statement)).scalar_one():
            return True
    return False


def downgrade(engine: Engine, target_version: int, migrations: Sequence[Migration] | None = None) -> tuple[int, ...]:
    """Downgrade only empty schemas with explicit ``.down.sql`` companions."""
    _require_postgresql(engine)
    revisions = tuple(migrations or discover_migrations())
    _validate_revision_sequence(revisions)
    by_version = {migration.version: migration for migration in revisions}
    if target_version < 0 or target_version not in {0, *by_version}:
        raise MigrationError("Target version must be zero or a known migration version.")

    downgraded: list[int] = []
    with _locked_connection(engine) as connection:
        with connection.begin():
            history = _read_history(connection)
            if not history:
                raise SchemaNotReady("Database has no migration history to downgrade.")
            _validate_version_set(history, revisions)
            current = max(history)
            if target_version >= current:
                return ()
            if _has_application_data(connection):
                raise UnsafeDowngrade("Downgrade refused because public application tables contain data.")
            down_files: dict[int, str] = {}
            for version in range(current, target_version, -1):
                migration = by_version[version]
                down_path = migration.path.with_name(f"{migration.path.stem}.down.sql")
                if not down_path.is_file():
                    raise MigrationError(f"Migration {version:04d} has no reviewed downgrade script.")
                down_files[version] = down_path.read_text(encoding="utf-8")

        for version in range(current, target_version, -1):
            with connection.begin():
                connection.exec_driver_sql(down_files[version])
                connection.execute(
                    text("DELETE FROM public.schema_migrations WHERE version = :version"),
                    {"version": version},
                )
            downgraded.append(version)
    return tuple(downgraded)
