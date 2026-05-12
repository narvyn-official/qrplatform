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
fi

# Collect static
if [ "$COLLECTSTATIC" = true ]; then
  echo "→ Collecting static files..."
  docker compose run --rm web python manage.py collectstatic --noinput --clear
fi

# Rolling restart (zero downtime)
echo "→ Restarting services..."
if docker compose config | awk '
  $1 == "web:" { in_web = 1; next }
  in_web && $0 ~ /^  [A-Za-z0-9_-]+:/ { in_web = 0 }
  in_web && $1 == "ports:" { found = 1 }
  END { exit found ? 0 : 1 }
'; then
  echo "→ Published web port detected; recreating the single web container..."
  docker compose up -d --no-deps --force-recreate web
else
  docker compose up -d --no-deps --scale web=2 web
  sleep 10
  docker compose up -d --no-deps --scale web=1 web
fi

docker compose up -d --no-deps celery_worker celery_beat

echo "→ Cleaning up old images..."
docker image prune -f

echo "✓ Deployment complete!"
docker compose ps
