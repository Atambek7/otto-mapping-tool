#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

if [[ -z "${OTTORESTART_TEST_DATABASE_URL:-}" ]]; then
  echo "Set OTTORESTART_TEST_DATABASE_URL to a disposable local PostgreSQL 15 test server." >&2
  exit 2
fi

python3 - <<'PY'
import os
import sys
from urllib.parse import urlsplit

test_dsn = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "")
parts = urlsplit(test_dsn)
database = parts.path.lstrip("/").split("/", 1)[0].split("?", 1)[0].lower()
if parts.scheme not in {"postgresql", "postgresql+psycopg"}:
    raise SystemExit("Test DSN must use PostgreSQL with psycopg.")
if parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("Test DSN must target a local PostgreSQL server.")
if "test" not in database:
    raise SystemExit("Test database name must contain 'test'.")
for variable in ("OTTORESTART_DATABASE_URL", "OTTORESTART_MIGRATION_DATABASE_URL"):
    configured = os.environ.get(variable, "").strip()
    if configured and configured == test_dsn:
        raise SystemExit("Application, migration, and test database URLs must be distinct.")
PY

# Tests receive only blocked app/migration URLs; PostgreSQL suites opt in through
# the separately validated disposable test server above.
export OTTORESTART_DATABASE_URL="postgresql+psycopg://blocked:blocked@127.0.0.1:1/ottorestart_blocked_test"
export OTTORESTART_MIGRATION_DATABASE_URL="postgresql+psycopg://blocked:blocked@127.0.0.1:1/ottorestart_blocked_test"
export OTTORESTART_MODE=LEGACY_READ_ONLY
export OTTORESTART_LOCAL_WRITES_ENABLED=false
export OTTORESTART_OTTO_READ_ENABLED=false
export OTTORESTART_AFTERCOOL_READ_ENABLED=false
export OTTORESTART_OPENAI_ENABLED=false
export OTTORESTART_PRODUCT_WRITE_ENABLED=false
export OTTORESTART_AVAILABILITY_WRITE_ENABLED=false
export OTTORESTART_DISCOVERY_SCHEDULE_ENABLED=false
export OTTORESTART_CELERY_ENABLED=false
export OTTORESTART_GTIN_APPROVED_EVIDENCE_TYPES=
export OTTORESTART_OTTO_JV_CLIENT_ID=
export OTTORESTART_OTTO_JV_CLIENT_SECRET=
export OTTORESTART_OTTO_XL_CLIENT_ID=
export OTTORESTART_OTTO_XL_CLIENT_SECRET=
export OTTORESTART_AFTERCOOL_USERNAME=
export OTTORESTART_AFTERCOOL_PASSWORD=
export OTTORESTART_OPENAI_API_KEY=

cd "$BACKEND_DIR"
python3 -m compileall -q .
python3 -m pytest -q

cd "$ROOT_DIR"
if [[ ! -x node_modules/.bin/tsc ]]; then
  echo "Install the locked Restart frontend dependencies before running this check." >&2
  exit 2
fi
node_modules/.bin/tsc --noEmit --incremental false

umask 077
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ottorestart-release.XXXXXX")"
trap 'python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" "$tmp_dir"' EXIT

node_modules/.bin/tsc --strict --target ES2022 --module commonjs --moduleResolution node \
  --esModuleInterop --skipLibCheck --rootDir . --outDir "$tmp_dir" \
  features/review/demo-service.ts features/queue/demo-queue-service.ts features/demo/service.ts

export OTTORESTART_DEMO_TEST_MODULE="$tmp_dir/features/review/demo-service.js"
export OTTORESTART_DEMO_QUEUE_TEST_MODULE="$tmp_dir/features/queue/demo-queue-service.js"
export OTTORESTART_DEMO_WORKSPACE_TEST_MODULE="$tmp_dir/features/demo/service.js"
node --test tests/*.test.mjs tests/*.test.cjs
