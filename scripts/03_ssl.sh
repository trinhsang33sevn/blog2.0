#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 03_ssl.sh — Lấy SSL certificate từ Let's Encrypt
# Dùng: bash scripts/03_ssl.sh yourdomain.com your@email.com
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Dùng: bash scripts/03_ssl.sh <domain> <email>"
  echo "Ví dụ: bash scripts/03_ssl.sh autoblogspot.com admin@gmail.com"
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Dừng nginx nếu đang chạy (để certbot dùng port 80)
echo ">>> Tạm dừng nginx..."
docker compose stop nginx 2>/dev/null || true

echo ">>> Lấy cert cho $DOMAIN..."
sudo certbot certonly \
  --standalone \
  --non-interactive \
  --agree-tos \
  --email "$EMAIL" \
  -d "$DOMAIN" \
  -d "www.$DOMAIN"

echo ">>> Copy cert vào nginx/ssl/..."
mkdir -p nginx/ssl
sudo cp /etc/letsencrypt/live/"$DOMAIN"/fullchain.pem nginx/ssl/fullchain.pem
sudo cp /etc/letsencrypt/live/"$DOMAIN"/privkey.pem   nginx/ssl/privkey.pem
sudo chown "$USER":"$USER" nginx/ssl/*.pem
chmod 600 nginx/ssl/privkey.pem

echo ">>> Khởi động lại nginx..."
docker compose up -d nginx

echo ""
echo "✅ SSL cert đã cài cho $DOMAIN"
echo "   Cert hết hạn sau 90 ngày — tự gia hạn qua cron (xem bên dưới)"
echo ""
echo "   Thêm cron để tự gia hạn (chạy lệnh sau):"
echo "   (crontab -l 2>/dev/null; echo '0 3 * * * bash $REPO_DIR/scripts/03_ssl.sh $DOMAIN $EMAIL') | crontab -"
