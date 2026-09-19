#!/usr/bin/env bash
# Update a deployed server from the canonical GitHub repository.
# Server-only files (backend/.env and SQLite data) are gitignored and retained.
set -Eeuo pipefail

APP_DIR="/opt/ashare-market-monitor"
BRANCH="main"
SERVICES=(
  ashare-frontend.service
  ashare-api.service
  ashare-collector.service
  ashare-wind-collector.service
  ashare-vix-collector.service
  ashare-factor-collector.service
)

cd "$APP_DIR"

echo "Stopping services…"
sudo systemctl stop "${SERVICES[@]}" || true

echo "Pulling the canonical repository…"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "Installing backend dependencies…"
backend/.venv/bin/pip install --disable-pip-version-check --no-input -r backend/requirements.txt

echo "Building frontend…"
(
  cd frontend
  npm install --no-audit --no-fund
  NEXT_PUBLIC_API_BASE_URL= NODE_OPTIONS=--max-old-space-size=768 npm run build
)

echo "Refreshing service definitions…"
sudo cp deploy/systemd/ashare-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICES[@]}"
sudo systemctl start "${SERVICES[@]}"

echo "Deployment complete."
