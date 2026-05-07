#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 01_setup_server.sh — Chạy 1 lần trên VPS mới (Ubuntu 22.04 / Debian 12)
# Cài: Docker, Docker Compose v2, Certbot, Git, ufw firewall
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

echo ">>> Cập nhật hệ thống..."
sudo apt-get update -y && sudo apt-get upgrade -y

echo ">>> Cài Docker..."
sudo apt-get install -y ca-certificates curl gnupg lsb-release git ufw

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# Thêm user hiện tại vào group docker (không cần sudo mỗi lần)
sudo usermod -aG docker "$USER"

echo ">>> Cài Certbot (Let's Encrypt)..."
sudo apt-get install -y certbot

echo ">>> Cấu hình UFW Firewall..."
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status

echo ""
echo "✅ Setup xong!"
echo "   ⚠️  Log out và SSH lại để docker group có hiệu lực."
echo "   Tiếp theo: chạy  02_deploy.sh"
