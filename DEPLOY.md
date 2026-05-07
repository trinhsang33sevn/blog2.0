# Deploy AutoBlogspot lên Google Cloud (GCE)

## Khuyến nghị: e2-small (2 vCPU, 2GB RAM) — ~$17/tháng

---

## Bước 1 — Tạo VM trên Google Cloud Console

1. Vào [console.cloud.google.com](https://console.cloud.google.com) → **Compute Engine** → **VM instances** → **Create Instance**

2. Cấu hình:
   | Mục | Giá trị |
   |-----|---------|
   | Name | `autoblogspot` |
   | Region | `asia-southeast1` (Singapore — gần VN nhất) |
   | Machine type | `e2-small` (2 vCPU, 2GB RAM) |
   | Boot disk | Ubuntu 22.04 LTS, 20GB SSD |
   | Firewall | ✅ Allow HTTP traffic, ✅ Allow HTTPS traffic |

3. Click **Create** → đợi ~1 phút → ghi lại **External IP**

---

## Bước 2 — Trỏ domain về VPS

Vào DNS của domain, thêm 2 bản ghi:

| Type | Name | Value |
|------|------|-------|
| A | `@` | `<External IP>` |
| A | `www` | `<External IP>` |

> Đợi DNS propagate ~5-30 phút. Kiểm tra: `ping yourdomain.com`

---

## Bước 3 — Kết nối SSH và cài Docker

```bash
# Từ Google Cloud Console: VM instances → SSH (hoặc dùng gcloud CLI)
gcloud compute ssh autoblogspot --zone=asia-southeast1-b
```

Trên VM, chạy:
```bash
git clone https://github.com/YOUR_USERNAME/autoblogspot.git
cd autoblogspot

bash scripts/01_setup_server.sh

# Đăng xuất và SSH lại để docker group có hiệu lực
exit
```

---

## Bước 4 — Cấu hình .env

```bash
cd autoblogspot

cp .env.example .env
nano .env
```

Điền các giá trị bắt buộc:

```env
# Tạo SECRET_KEY:
# python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<generated-key>

DEBUG=false
BASE_URL=https://yourdomain.com

DATABASE_URL=postgresql://autoblogspot:<db-password>@postgres:5432/autoblogspot
POSTGRES_PASSWORD=<db-password>   # đặt mật khẩu mạnh

HOST=0.0.0.0
PORT=8000

# Email (tùy chọn, cần cho tính năng forgot password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-gmail@gmail.com
SMTP_PASS=<gmail-app-password>
SMTP_FROM=AutoBlogspot <noreply@yourdomain.com>
SMTP_TLS=true
```

---

## Bước 5 — Cập nhật domain trong nginx.conf

```bash
# Thay autoblogspot.com bằng domain thật
sed -i 's/autoblogspot.com/yourdomain.com/g' nginx/nginx.conf
```

---

## Bước 6 — Lấy SSL Certificate (Let's Encrypt)

```bash
bash scripts/03_ssl.sh yourdomain.com your@email.com
```

Script sẽ tự động:
- Dừng nginx tạm thời
- Lấy cert từ Let's Encrypt
- Copy cert vào `nginx/ssl/`
- Khởi động lại nginx

---

## Bước 7 — Deploy

```bash
bash scripts/02_deploy.sh
```

Kiểm tra:
```bash
docker compose ps          # Tất cả phải "healthy"
docker compose logs app    # Xem log app
curl https://yourdomain.com/health  # Phải trả về {"status":"ok"}
```

---

## Tự động gia hạn SSL (mỗi 3 tháng)

```bash
# Thêm cron job
(crontab -l 2>/dev/null; echo "0 3 1 * * cd /home/$USER/autoblogspot && bash scripts/03_ssl.sh yourdomain.com your@email.com >> logs/ssl_renew.log 2>&1") | crontab -
```

---

## Cập nhật code

Mỗi khi có code mới:
```bash
bash scripts/04_update.sh
```

---

## Xem logs

```bash
docker compose logs -f app      # Log app realtime
docker compose logs -f nginx    # Log nginx
docker compose logs postgres    # Log database
tail -f logs/app.log            # Log file của app
```

---

## Khởi động lại dịch vụ

```bash
docker compose restart app     # Chỉ restart app
docker compose restart nginx   # Chỉ restart nginx
docker compose down && docker compose up -d   # Restart toàn bộ
```

---

## Sau khi deploy — Cấu hình trong Admin

Đăng nhập với tài khoản admin → **/admin** để cài:
- **SePay**: bank account, API key, webhook token
- **LemonSqueezy**: API key, store ID, variant IDs, webhook secret
- Kiểm tra users, subscription plans

Đăng nhập vào **/settings** để cài:
- **OpenRouter / Gemini / Claude / OpenAI / Groq** API keys
- **Sinbyte** API key (indexing)
- **Pixabay** API key (ảnh miễn phí)
- **WordPress / Tumblr / Google** OAuth credentials

---

## Chi phí ước tính (GCE)

| Dịch vụ | Chi phí |
|---------|---------|
| e2-small VM (24/7) | ~$17/tháng |
| 20GB SSD | ~$2/tháng |
| Outbound bandwidth (10GB) | ~$1/tháng |
| **Tổng** | **~$20/tháng** |

> 💡 Dùng **e2-micro** (free tier, 1 vCPU, 1GB) nếu muốn miễn phí — nhưng sẽ chậm với nhiều users đồng thời.
