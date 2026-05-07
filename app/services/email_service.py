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
