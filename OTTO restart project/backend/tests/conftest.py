from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


def pytest_sessionstart(session) -> None:
    """Force application imports to use an unreachable URL, never a configured DB."""
    # Also overrides dotenv values loaded by application modules after this hook.
    os.environ["OTTORESTART_DATABASE_URL"] = (
        "postgresql+psycopg://blocked:blocked@127.0.0.1:1/ottorestart_blocked_test"
    )


def _test_server_url() -> URL | None:
    raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
    if not raw_url:
        return None

    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError("OTTORESTART_TEST_DATABASE_URL must use PostgreSQL.")
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise pytest.UsageError("Migration integration tests accept only a loopback PostgreSQL server.")
    database_name = (url.database or "").lower()
    if "test" not in database_name:
        raise pytest.UsageError("The explicit test database name must contain 'test'.")

    app_url = os.environ.get("OTTORESTART_DATABASE_URL", "").strip()
    if app_url and make_url(app_url) == url:
        raise pytest.UsageError("Test and application database URLs must not be the same.")
    return url


@pytest.fixture
def isolated_postgres_database() -> Iterator[URL]:
    """Create a unique temporary database on an explicitly opted-in local PG15 server."""
    base_url = _test_server_url()
    if base_url is None:
        pytest.skip("Set OTTORESTART_TEST_DATABASE_URL to run isolated PostgreSQL migration tests.")

    admin_url = base_url.set(database="postgres")
    admin_engine = create_engine(admin_url, pool_pre_ping=True)
    database_name = f"ottorestart_s01_test_{uuid.uuid4().hex}"
    quote = admin_engine.dialect.identifier_preparer.quote_identifier
    try:
        with admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
            if not 150000 <= version < 160000:
                raise pytest.UsageError("Migration integration tests require PostgreSQL 15.")
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(database_name)} TEMPLATE template0")

        test_url = base_url.set(database=database_name)
        try:
            yield test_url
        finally:
            with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(database_name)}")
    finally:
        admin_engine.dispose()
