#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Production deployment script
# Usage: ./scripts/deploy.sh [--no-migrate] [--no-collectstatic]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

MIGRATE=true
COLLECTSTATIC=true

for arg in "$@"; do
  case $arg in
    --no-migrate) MIGRATE=false ;;
    --no-collectstatic) COLLECTSTATIC=false ;;
  esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  QRFlow Deployment Script"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Pull latest code
echo "→ Pulling latest code..."
git pull origin main

# Build production image
echo "→ Building Docker image..."
docker compose build --pull web celery_worker celery_beat

# Run migrations
if [ "$MIGRATE" = true ]; then
  echo "→ Running database migrations..."
  docker compose run --rm web python manage.py migrate --noinput
  echo "→ Processing pending scan events..."
  docker compose run --rm web python manage.py process_pending_scan_events --limit 1000
fi

# Collect static
if [ "$COLLECTSTATIC" = true ]; then
  echo "→ Collecting static files..."
  docker compose run --rm web python manage.py collectstatic --noinput --clear
fi

# Restart application services.
echo "→ Restarting services..."
docker compose up -d --no-deps --scale web=1 --force-recreate web

docker compose up -d --no-deps celery_worker celery_beat

echo "→ Cleaning up old images..."
docker image prune -f

echo "✓ Deployment complete!"
docker compose ps
