# OttoRestart runtime and operations

This runbook describes the isolated Compose runtime. It does not authorize a live OTTO/Aftercool call, product mutation, migration against a working database, or worker/scheduler activation.

## Runtime layout

- `restart_frontend` serves the Next.js production build on container port 3001 and is published only on host loopback, default `127.0.0.1:3002`.
- `restart_backend` serves FastAPI on port 3301 inside the Compose network. It has no host-published port; the Next.js BFF is its only normal HTTP entry point.
- `restart_db` is PostgreSQL 15. Its named volume remains `otto_restart_postgres_data`; do not remove it with `docker compose down -v`.
- Redis, the Celery worker, the outbox dispatcher, and Celery Beat are opt-in Compose profiles. The PostgreSQL job ledger is durable; Redis is only transport and has persistence disabled.
- GTIN evidence bytes are stored in PostgreSQL, so PostgreSQL backups include those artifacts. No public artifact directory is mounted.
- The Compose network is OttoRestart-specific. It does not attach to OttoFlow networks, databases, queues, or containers.

### Standalone preview connectivity

The current `otto-restart-preview` development container is started separately from Compose. It must join `ottorestartproject_ottorestart` to reach the Compose backend:

```bash
docker network connect ottorestartproject_ottorestart otto-restart-preview
```

Run this only when the preview is not already attached, and repeat it after recreating the preview container. Compose declares `otto_restart_backend` as a stable backend network alias for the preview's development BFF fallback; the production frontend uses the service name `restart_backend`.

After backend recreation, verify a read request through the authenticated frontend BFF as well as backend health. Internal backend health alone does not prove preview DNS/network reachability. A `BACKEND_UNAVAILABLE` response can indicate a disconnected preview even when backend health is green.

## Configuration and startup

1. Copy `.env.example` to `.env`, set file permissions to owner-only, and populate required values through the approved secret store. Never commit `.env` or place secrets in build arguments. Use separate random values for `OTTORESTART_AUTH_SECRET` and `OTTORESTART_INTERNAL_AUTH_SECRET`; use an URL-safe random `POSTGRES_PASSWORD` because Compose builds the psycopg URL from it.
2. Set `OTTORESTART_ALLOWED_ORIGINS` to the exact public HTTPS origin. `OTTORESTART_MODE` defaults to `LEGACY_READ_ONLY`; all local and external feature flags default off. Product and availability write flags must remain off until separately approved.
3. Review the resolved Compose file without printing it (it contains secrets):

   ```bash
   docker compose config --quiet
   ```

4. Start PostgreSQL only after confirming the configured volume is the intended OttoRestart database:

   ```bash
   docker compose up -d restart_db
   ```

5. Apply migrations only as a separate, reviewed operator action against the intended OttoRestart database. The API startup never applies DDL:

   ```bash
   docker compose run --rm --no-deps restart_backend python migrate.py upgrade
   ```

6. After schema head and credentials are verified, start the API and frontend:

   ```bash
   docker compose up -d restart_backend restart_frontend
   ```

The backend health check is database-backed and the frontend health check exercises the local CSRF endpoint. A schema mismatch prevents the API from becoming ready. Neither check makes an external request.

The Next.js public runtime mode is embedded at image build time. If `OTTORESTART_MODE` changes, rebuild the frontend image before deployment so the UI mode and backend mode agree.

## Background profiles

The default `docker compose up` does not start Redis, workers, dispatcher, or Beat. The `workers` profile starts Redis, one Celery worker and the PostgreSQL outbox dispatcher. The currently deployed manual-import fence permits only `import.process` jobs created at or after `OTTORESTART_MANUAL_IMPORTS_AFTER`; older queued jobs and discovery jobs are left untouched. Product, availability, and Drip Feed write executors are excluded. The `scheduler` profile remains off; weekly discovery is disabled. Schema head 0017 is applied in the current environment.

Workers were activated on 2026-11-10 for the approved manual-import path only. At activation, three pre-existing imports and one discovery job remained pending because they predate the cutoff or are not allowlisted. No import was submitted during activation, so no OTTO/Aftercool requests occurred. A submitted manual import may issue read-only calls only for its supplied EANs; product and availability writes remain disabled. The current DB container was attached manually to the Restart-specific network after Compose created it; repeat that attachment if the DB container is recreated. Beat remains stopped.

## TLS and reverse proxy

Terminate TLS at the organization's approved reverse proxy. Do not expose ports 3301, 5432, or 6379 publicly. The frontend is bound to loopback and should be the only upstream. Example Nginx location (replace the hostname and certificate paths in the host configuration):

```nginx
server {
    listen 443 ssl;
    server_name restart.example.invalid;

    # Configure ssl_certificate and ssl_certificate_key in the managed host config.

    location = /restart {
        return 308 /restart/;
    }

    location /restart/ {
        proxy_pass http://127.0.0.1:3002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Set `OTTORESTART_ALLOWED_ORIGINS` to `https://restart.example.invalid` (the actual deployed origin). Do not add a direct public route to FastAPI. Preserve the `/restart` prefix through the proxy.

## Graceful shutdown and recovery

- Uvicorn has a 30-second graceful shutdown timeout; Compose allows 40 seconds for API/frontend termination. Celery workers receive a 90-second grace period to finish or safely release leases. The dispatcher handles SIGTERM/SIGINT and stops between bounded polls.
- If Redis is unavailable, PostgreSQL jobs remain pending and the dispatcher retries later. Duplicate broker delivery is fenced by job leases and idempotent handlers. Do not delete PostgreSQL jobs to clear a broker backlog.
- For backup, write to an access-controlled path with restrictive permissions. Example from the host:

  ```bash
  umask 077
  docker compose exec -T restart_db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump --format=custom --no-owner -h 127.0.0.1 -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP_PATH"
  ```

- Rehearse restore only into a fresh isolated PostgreSQL database and volume. Stop workers before restore validation; verify migration head, row counts, audit history, and GTIN evidence hashes. Do not use `--clean` or restore over the current/production volume as a routine recovery step. A production cutover or destructive restore requires its own approval and backup review.
- Keep backup encryption, retention, off-host storage, and restore credentials under the organization's existing database operations policy. The Compose file does not create or transmit backups automatically.

## Current verification boundary

`docker compose config --quiet` is a static configuration check. No API server, Compose service, worker, Beat process, migration, or external adapter should be started during offline code verification. See `S00_CHECKPOINT.md` for stage-level test results and deferred policy questions.
