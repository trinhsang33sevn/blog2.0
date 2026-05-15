import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import get_settings

logger = logging.getLogger("autoblogspot.email")
settings = get_settings()


def send_email(to: str, subject: str, html: str) -> bool:
    if not settings.email_configured:
        logger.warning("Email not configured — skipping send to %s", to)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
            smtp.sendmail(settings.SMTP_FROM, [to], msg.as_string())

        logger.info("Email sent to %s — %s", to, subject)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def send_contact_email(name: str, sender_email: str, message: str, admin_email: str) -> bool:
    subject = f"[AutoBlogspot] Liên hệ mới từ {name}"
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f0f2f5;padding:40px 0;margin:0">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
    <div style="background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:28px 32px;text-align:center">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:700">AutoBlogspot</h1>
      <p style="color:rgba(255,255,255,.8);margin:6px 0 0;font-size:13px">Tin nhắn liên hệ mới</p>
    </div>
    <div style="padding:28px 32px">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:8px 0;color:#6b7280;width:110px;vertical-align:top;">Tên:</td>
            <td style="padding:8px 0;color:#111827;font-weight:600;">{name}</td></tr>
        <tr><td style="padding:8px 0;color:#6b7280;vertical-align:top;">Email:</td>
            <td style="padding:8px 0;"><a href="mailto:{sender_email}" style="color:#4f46e5;">{sender_email}</a></td></tr>
      </table>
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0;">
      <p style="color:#374151;font-size:14px;font-weight:600;margin:0 0 8px">Nội dung:</p>
      <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:16px;color:#374151;font-size:14px;line-height:1.7;white-space:pre-wrap;">{message}</div>
    </div>
    <div style="background:#f9fafb;padding:14px 32px;text-align:center;border-top:1px solid #e5e7eb">
      <p style="color:#9ca3af;font-size:12px;margin:0">© 2025 AutoBlogspot — autoblogspot.com</p>
    </div>
  </div>
</body>
</html>"""
    return send_email(admin_email, subject, html)


def send_verification_email(to: str, code: str, full_name: str = "") -> bool:
    subject = "Mã xác minh đăng ký AutoBlogspot"
    name_part = f"Xin chào {full_name}," if full_name else "Xin chào,"
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f0f2f5;padding:40px 0;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
    <div style="background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:32px 32px 24px;text-align:center">
      <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700">AutoBlogspot</h1>
      <p style="color:rgba(255,255,255,.8);margin:8px 0 0;font-size:14px">Xác minh địa chỉ email</p>
    </div>
    <div style="padding:32px">
      <p style="color:#374151;font-size:15px;margin:0 0 8px">{name_part}</p>
      <p style="color:#374151;font-size:15px;margin:0 0 24px">Đây là mã xác minh để hoàn tất đăng ký tài khoản AutoBlogspot:</p>
      <div style="background:#f5f3ff;border:2px dashed #7c3aed;border-radius:12px;padding:24px;text-align:center;margin:0 0 24px">
        <div style="font-size:40px;font-weight:900;letter-spacing:12px;color:#4f46e5;font-family:monospace">{code}</div>
        <div style="color:#6b7280;font-size:13px;margin-top:8px">Mã có hiệu lực trong <strong>15 phút</strong></div>
      </div>
      <p style="color:#6b7280;font-size:13px;margin:0">Nếu bạn không yêu cầu đăng ký, hãy bỏ qua email này.</p>
    </div>
    <div style="background:#f9fafb;padding:14px 32px;text-align:center;border-top:1px solid #e5e7eb">
      <p style="color:#9ca3af;font-size:12px;margin:0">© 2025 AutoBlogspot — autoblogspot.com</p>
    </div>
  </div>
</body>
</html>"""
    return send_email(to, subject, html)


def send_system_alert(to: str, alerts: list[dict], stats: dict) -> bool:
    """Gửi email cảnh báo hệ thống khi disk/CPU/RAM vượt ngưỡng."""
    def _bar_color(pct: float) -> str:
        return "#ef4444" if pct >= 85 else "#f59e0b" if pct >= 70 else "#10b981"

    def _pct_bar(pct: float) -> str:
        color = _bar_color(pct)
        return (
            f'<div style="background:#e5e7eb;border-radius:4px;height:8px;margin:4px 0 2px">'
            f'<div style="background:{color};width:{min(pct,100):.0f}%;height:8px;border-radius:4px"></div>'
            f'</div>'
        )

    alert_rows = "".join(
        f'<tr><td style="padding:6px 0;color:#374151;font-weight:600">{a["icon"]} {a["label"]}</td>'
        f'<td style="padding:6px 0;color:#ef4444;font-weight:700">{a["value"]}</td>'
        f'<td style="padding:6px 0;color:#6b7280">{a["action"]}</td></tr>'
        for a in alerts
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f0f2f5;padding:40px 0;margin:0">
  <div style="max-width:560px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
    <div style="background:linear-gradient(135deg,#dc2626,#b91c1c);padding:24px 32px;text-align:center">
      <h1 style="color:#fff;margin:0;font-size:20px;font-weight:700">⚠️ AutoBlogspot — Cảnh báo hệ thống</h1>
      <p style="color:rgba(255,255,255,.8);margin:6px 0 0;font-size:13px">Server {stats.get('hostname','')}</p>
    </div>
    <div style="padding:28px 32px">
      <p style="color:#374151;font-size:14px;margin:0 0 20px">Hệ thống phát hiện một hoặc nhiều chỉ số vượt ngưỡng cảnh báo:</p>
      <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:20px">
        <thead><tr style="border-bottom:2px solid #e5e7eb">
          <th style="text-align:left;padding:6px 0;color:#6b7280">Chỉ số</th>
          <th style="text-align:left;padding:6px 0;color:#6b7280">Giá trị</th>
          <th style="text-align:left;padding:6px 0;color:#6b7280">Gợi ý</th>
        </tr></thead>
        <tbody>{alert_rows}</tbody>
      </table>
      <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin-bottom:20px">
        <p style="margin:0 0 12px;font-weight:600;color:#374151;font-size:13px">📊 Tổng quan hệ thống hiện tại:</p>
        <div style="font-size:12px;color:#374151">
          <div style="margin-bottom:8px">
            <span style="font-weight:600">CPU:</span> {stats.get('cpu_pct',0):.1f}% ({stats.get('cpu_cores',0)} nhân)
            {_pct_bar(stats.get('cpu_pct',0))}
          </div>
          <div style="margin-bottom:8px">
            <span style="font-weight:600">RAM:</span> {stats.get('mem_used_gb',0)} GB / {stats.get('mem_total_gb',0)} GB ({stats.get('mem_pct',0):.1f}%)
            {_pct_bar(stats.get('mem_pct',0))}
          </div>
          <div>
            <span style="font-weight:600">Ổ cứng:</span> {stats.get('disk_used_gb',0):.1f} GB / {stats.get('disk_total_gb',0):.1f} GB ({stats.get('disk_pct',0):.1f}%)
            {_pct_bar(stats.get('disk_pct',0))}
          </div>
        </div>
      </div>
      <div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;padding:12px;font-size:12px;color:#92400e">
        <strong>💡 Lệnh dọn dẹp nhanh (SSH vào server):</strong><br>
        <code style="font-family:monospace">sudo docker builder prune -af</code> — Xóa Docker build cache<br>
        <code style="font-family:monospace">sudo journalctl --vacuum-size=100M</code> — Giảm system journal<br>
        <code style="font-family:monospace">sudo find /var/log -name "*.gz" -delete</code> — Xóa log nén cũ
      </div>
    </div>
    <div style="background:#f9fafb;padding:14px 32px;text-align:center;border-top:1px solid #e5e7eb">
      <a href="https://autoblogspot.com/admin?tab=system" style="color:#4f46e5;font-size:12px">Xem Admin Dashboard</a>
      <span style="color:#d1d5db;margin:0 8px">|</span>
      <span style="color:#9ca3af;font-size:12px">© 2025 AutoBlogspot</span>
    </div>
  </div>
</body></html>"""
    subject = f"⚠️ AutoBlogspot: Cảnh báo hệ thống — {', '.join(a['label'] for a in alerts)}"
    return send_email(to, subject, html)


def send_password_reset(to: str, reset_url: str) -> bool:
    subject = "Đặt lại mật khẩu AutoBlogspot"
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f0f2f5;padding:40px 0;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
    <div style="background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:32px 32px 24px;text-align:center">
      <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700">AutoBlogspot</h1>
      <p style="color:rgba(255,255,255,.8);margin:8px 0 0;font-size:14px">Đặt lại mật khẩu của bạn</p>
    </div>
    <div style="padding:32px">
      <p style="color:#374151;font-size:15px;margin:0 0 16px">Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản liên kết với email này.</p>
      <p style="color:#374151;font-size:15px;margin:0 0 24px">Nhấn nút bên dưới để đặt mật khẩu mới. Liên kết có hiệu lực trong <strong>30 phút</strong>.</p>
      <div style="text-align:center;margin:28px 0">
        <a href="{reset_url}" style="background:#4f46e5;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;display:inline-block">
          Đặt lại mật khẩu
        </a>
      </div>
      <p style="color:#6b7280;font-size:13px;margin:24px 0 0">Nếu bạn không yêu cầu điều này, hãy bỏ qua email này. Mật khẩu của bạn sẽ không thay đổi.</p>
      <p style="color:#6b7280;font-size:12px;margin:8px 0 0;word-break:break-all">Hoặc dán link: {reset_url}</p>
    </div>
    <div style="background:#f9fafb;padding:16px 32px;text-align:center;border-top:1px solid #e5e7eb">
      <p style="color:#9ca3af;font-size:12px;margin:0">© 2025 AutoBlogspot — autoblogspot.com</p>
    </div>
  </div>
</body>
</html>"""
    return send_email(to, subject, html)
