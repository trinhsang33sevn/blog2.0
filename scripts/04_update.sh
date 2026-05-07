#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 04_update.sh — Cập nhật code từ Git và restart app (zero-downtime)
# Chạy: bash scripts/04_update.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo ">>> Git pull..."
git pull origin main

echo ">>> Rebuild & restart app (không ảnh hưởng postgres)..."
docker compose build --no-cache app
docker compose up -d --no-deps app

echo ">>> Chờ app healthy..."
for i in $(seq 1 24); do
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health || echo "000")
  if [[ "$HTTP" == "200" ]]; then
    echo "✅ App healthy!"
    break
  fi
  echo "   Chờ ($i/24)..."
  sleep 5
done

docker compose ps
