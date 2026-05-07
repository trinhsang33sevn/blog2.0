#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 02_deploy.sh — Deploy lần đầu hoặc cập nhật mã nguồn
# Chạy từ thư mục gốc dự án: bash scripts/02_deploy.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# ── Kiểm tra .env ────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo "❌ Không tìm thấy .env — hãy tạo từ .env.example:"
  echo "   cp .env.example .env && nano .env"
  exit 1
fi

# Cảnh báo nếu .env vẫn là giá trị mặc định
if grep -q "your-secret-key-here" .env; then
  echo "❌ .env chứa SECRET_KEY mặc định — hãy đổi trước khi deploy!"
  echo "   python3 -c \"import secrets; print(secrets.token_hex(32))\""
  exit 1
fi

# ── Kiểm tra SSL cert ────────────────────────────────────────────────────────
if [[ ! -f nginx/ssl/fullchain.pem ]]; then
  echo "❌ Chưa có SSL cert tại nginx/ssl/fullchain.pem"
  echo "   Chạy: bash scripts/03_ssl.sh your-domain.com your@email.com"
  exit 1
fi

echo ">>> Pull image & build..."
docker compose pull --ignore-pull-failures 2>/dev/null || true
docker compose build --no-cache

echo ">>> Khởi động stack..."
docker compose up -d

echo ">>> Chờ app healthy..."
for i in $(seq 1 30); do
  STATUS=$(docker compose ps app --format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Health','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$STATUS" == "healthy" ]]; then
    echo "✅ App đang chạy!"
    break
  fi
  echo "   Chờ ($i/30)..."
  sleep 5
done

docker compose ps
echo ""
echo "✅ Deploy hoàn tất! Truy cập: $(grep BASE_URL .env | cut -d= -f2)"
