#!/usr/bin/env bash
# Единственная команда живого пути: собранное агентами приложение → публичный URL.
# rsync исходников на VPS → docker compose up --build → healthcheck → печать URL.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/.env"

: "${VPS_HOST:?set VPS_HOST in .env (например user@1.2.3.4)}"
: "${DEMO_DOMAIN:?set DEMO_DOMAIN in .env}"
APP_DIR="${1:?usage: deploy.sh <path-to-app-built-by-agents>}"
[ -f "$APP_DIR/requirements.txt" ] || { echo "❌ нет $APP_DIR/requirements.txt — нарушен контракт"; exit 1; }

REMOTE_DIR="/opt/demo"

echo "→ rsync приложения на $VPS_HOST:$REMOTE_DIR/app"
ssh "$VPS_HOST" "mkdir -p $REMOTE_DIR/app"
rsync -az --delete "$APP_DIR/" "$VPS_HOST:$REMOTE_DIR/app/"

echo "→ фиксированный Dockerfile + compose на VPS"
scp -q "$HERE/Dockerfile"               "$VPS_HOST:$REMOTE_DIR/app/Dockerfile"
scp -q "$HERE/docker-compose.app.yml"   "$VPS_HOST:$REMOTE_DIR/docker-compose.app.yml"
scp -q "$HERE/.env"                     "$VPS_HOST:$REMOTE_DIR/.env"

echo "→ сборка + старт app на VPS"
ssh "$VPS_HOST" "cd $REMOTE_DIR && docker compose -f docker-compose.app.yml up -d --build"

echo "→ healthcheck https://$DEMO_DOMAIN/health"
code=""
for _ in $(seq 1 30); do
	code=$(curl -s -o /dev/null -w '%{http_code}' "https://$DEMO_DOMAIN/health" || true)
	[ "$code" = "200" ] && { echo "✅ LIVE: https://$DEMO_DOMAIN"; exit 0; }
	sleep 2
done
echo "❌ healthcheck не прошёл за 60с (последний код: $code)"
echo "   debug: ssh $VPS_HOST 'cd $REMOTE_DIR && docker compose -f docker-compose.app.yml logs --tail=50 app'"
exit 1
