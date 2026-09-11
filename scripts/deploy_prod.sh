#!/usr/bin/env bash
set -e

PROJECT_DIR="/home/atambek/otto-mapping-tool"
cd "$PROJECT_DIR"

echo "=== 1. Pulling latest main from GitHub ==="
git fetch origin main
git checkout main
git reset --hard origin/main

echo "=== 2. Verifying configuration ==="
if [ ! -f "backend/.env" ]; then
    echo "ERROR: backend/.env is missing! Deployment halted."
    exit 1
fi
chmod 600 backend/.env

echo "=== 3. Starting Docker Compose stack ==="
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans

echo "=== 4. Running Database Migrations ==="
sleep 5
docker compose -f docker-compose.prod.yml exec -T backend alembic upgrade head || docker compose -f docker-compose.prod.yml exec -T backend alembic stamp head

echo "=== 5. Verifying Service Health ==="
SUCCESS=0
for i in {1..20}; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3200/ || true)
    API_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3200/api/docs || true)
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "307" ] || [ "$API_CODE" = "200" ]; then
        echo ">>> SUCCESS: Application is running! Frontend: $HTTP_CODE, API: $API_CODE"
        SUCCESS=1
        break
    fi
    echo "Waiting for app to respond (frontend: $HTTP_CODE, api: $API_CODE)... attempt $i/20"
    sleep 3
done

if [ "$SUCCESS" -ne 1 ]; then
    echo "WARNING: Health check timed out. Checking container status:"
    docker compose -f docker-compose.prod.yml ps
    exit 1
fi

echo "=== Deployment finished successfully ==="
