# Repository guide for Codex

## Project map

- `backend/`: FastAPI application, SQLAlchemy models, Alembic migrations, Celery tasks, OTTO/Aftercool integrations, and backend checks.
- `backend/routers/`: HTTP endpoints. `backend/services/`: integration and business logic.
- `frontend/`: Next.js app. Its `AGENTS.md` contains version-specific Next.js instructions; follow those when editing frontend code.
- `docker-compose.yml`: local stack (PostgreSQL, Redis, API, Celery worker/beat, frontend).
- `docker-compose.prod.yml`, `scripts/deploy_prod.sh`, and `.github/workflows/deploy.yml`: production deployment path.
- `implementation_plan.md` and `restart_3_12_months_plan.md`: planning documents, not proof that the described work or scripts exist. Confirm current code before implementing from a plan.
- If a README or plan conflicts with `docker-compose*.yml`, CI, or current code, treat the executable configuration/code as current and flag the documentation mismatch.

## Data and account boundaries

- The application supports separate OTTO accounts `JV` and `XL`. Product mappings and attributes are account-scoped; model uniqueness includes the account.
- Preserve the selected account through every query, service call, background task, and OTTO request. Do not infer the target account from a source product or use an unscoped EAN lookup when account context is available.
- Cross-account factory/product transfers have special handling, including account-specific brand and image rules. Inspect the existing service path before changing them.
- Treat credentials and `.env` files as secrets. Use `.env.example` for configuration names; never print, commit, or copy values from local environment files.

## Backend changes

- Keep HTTP routing in `backend/routers/` and reusable domain/integration logic in `backend/services/`.
- For schema changes, add a new revision under `backend/alembic/versions/`; do not rewrite an already-applied migration. Check both upgrade and downgrade behavior and preserve PostgreSQL compatibility.
- `backend/main.py` currently calls `Base.metadata.create_all()` at startup, while the repository also tracks Alembic migrations. Do not treat `create_all()` as a substitute for a migration when changing an existing schema.
- Celery work must remain safe to retry and should not load an unbounded catalog into memory. Follow existing task status, batching, and retry patterns.

## External and operational actions

- OTTO, Aftercool, and production operations can affect real accounts or listings. Before running a script, inspect its implementation and determine whether it makes network calls or writes/deletes/publishes data.
- Never run purge, republish, publish, migration, or production deployment commands against a live system unless the user explicitly requested that operation. Prefer a dry run when the script provides one.
- Keep live API calls out of unit checks. Use test fixtures or isolated local services for verification.

## Verification commands

The CI workflows are the source of truth for the currently exercised checks:

- Backend (Python 3.11, PostgreSQL 15, Redis 7): from `backend/`, run `python -m unittest discover tests`, `python test_furniture_rules.py`, and `python test_category_rules.py` with the CI-style environment and an isolated test database.
- Frontend (Node 20): from `frontend/`, run `npm ci` and `npm run build` with `NEXT_PUBLIC_API_URL=/api` and `API_URL=http://localhost:8001`.
- `frontend/package.json` also defines `npm run lint`.
- Current CI does not execute every `backend/test_*.py` file and does not exercise migrations by upgrading a fresh database: it creates the schema, then stamps Alembic at `head`. Do not describe those paths as CI-covered.

Do not initialize or stamp a database containing user data as part of testing. The CI-only helper `backend/scripts/ci_init_db.py` creates tables for an empty CI database; inspect it before using it anywhere else.

## Persistent Project Memory

Use `.codex/CURRENT_STATE.md` as the always-read checkpoint and `.codex/PROJECT_MEMORY.md` as the verified architecture and decision record. At the start of every Codex session, read this file and both memory files before acting. Read `.codex/SESSION_HISTORY.md` only when an older decision or incident is relevant.

At session start, inspect Git status, relevant diffs, and recent commits. Verify memory against the current repository; code and executable configuration take precedence over stale memory or plans. Correct memory when verified facts have changed. For continuation requests, resume at the next step in `CURRENT_STATE.md` without repeating completed work. For a new task, update that file to the new objective while retaining unresolved work that still matters.

Update `CURRENT_STATE.md` after meaningful steps, before risky or broad changes, after checks, and at context/compaction checkpoints. Update `PROJECT_MEMORY.md` after architecture, API, schema, dependency, or important design decisions. Add a short entry to `SESSION_HISTORY.md` when a major task or phase is completed. After context compaction, reread `CURRENT_STATE.md` before continuing.

Keep memory concise, path-specific, and explicit about verified facts versus assumptions and plans. Never store secrets, tokens, credentials, or `.env` values. Do not change application behavior to support memory. Leave `.codex/` files uncommitted unless the user explicitly asks to commit them; never stage or commit them automatically.

