import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..i18n import _ as t
from ..models import EmailVerification
from ..services.auth_service import (
    create_user, get_user_by_email, verify_password, hash_password,
    generate_reset_token, validate_reset_token, consume_reset_token,
)
from ..services.email_service import send_password_reset, send_verification_email
from ..templates import templates

logger = logging.getLogger("autoblogspot.auth")
settings = get_settings()

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {
            "error": t("auth_err_invalid_credentials")
        }, status_code=401)
    if not user.is_active:
        return templates.TemplateResponse(request, "login.html", {
            "error": t("auth_err_disabled")
        }, status_code=403)

    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register")
def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if password != password_confirm:
        return templates.TemplateResponse(request, "register.html", {
            "error": t("rp_err_mismatch"),
            "full_name": full_name, "email": email,
        }, status_code=400)

    if len(password) < 6:
        return templates.TemplateResponse(request, "register.html", {
            "error": t("rp_err_too_short"),
            "full_name": full_name, "email": email,
        }, status_code=400)

    if get_user_by_email(db, email):
        return templates.TemplateResponse(request, "register.html", {
            "error": t("auth_err_email_taken"),
            "full_name": full_name, "email": email,
        }, status_code=400)

    # If email not configured → skip OTP, create account directly
    if not settings.email_configured:
        user = create_user(db, email=email, password=password, full_name=full_name)
        request.session["user_id"] = user.id
        return RedirectResponse("/?success=Chao+mung+ban+den+voi+AutoBlogspot", status_code=303)

    # Generate OTP and save pending verification
    otp = f"{secrets.randbelow(1_000_000):06d}"
    pw_hash = hash_password(password)
    now = datetime.utcnow()

    # Remove any previous pending verification for this email
    db.query(EmailVerification).filter(EmailVerification.email == email.lower()).delete()
    pending = EmailVerification(
        email=email.strip().lower(),
        full_name=full_name.strip(),
        password_hash=pw_hash,
        otp_code=otp,
        expires_at=now + timedelta(minutes=15),
    )
    db.add(pending)
    db.commit()

    sent = send_verification_email(email, otp, full_name)
    if not sent:
        logger.warning("Failed to send OTP to %s — creating account directly", email)
        user = create_user(db, email=email, password=password, full_name=full_name)
        request.session["user_id"] = user.id
        return RedirectResponse("/?success=Chao+mung+ban+den+voi+AutoBlogspot", status_code=303)

    from urllib.parse import quote_plus
    return RedirectResponse(f"/verify-email?email={quote_plus(email)}", status_code=303)


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email_page(request: Request, email: str = ""):
    return templates.TemplateResponse(request, "verify_email.html", {
        "email": email,
        "error": request.query_params.get("error"),
    })


@router.post("/verify-email")
def verify_email(
    request: Request,
    email: str = Form(...),
    otp: str = Form(...),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote_plus
    email = email.strip().lower()
    otp   = otp.strip()

    pending = db.query(EmailVerification).filter(
        EmailVerification.email == email
    ).first()

    if not pending:
        return RedirectResponse(
            f"/register?error={quote_plus('Phiên xác minh không tồn tại. Vui lòng đăng ký lại.')}",
            status_code=303,
        )

    if datetime.utcnow() > pending.expires_at:
        db.delete(pending)
        db.commit()
        return RedirectResponse(
            f"/register?error={quote_plus('Mã đã hết hạn. Vui lòng đăng ký lại.')}",
            status_code=303,
        )

    if pending.attempts >= 5:
        db.delete(pending)
        db.commit()
        return RedirectResponse(
            f"/register?error={quote_plus('Nhập sai quá nhiều lần. Vui lòng đăng ký lại.')}",
            status_code=303,
        )

    if otp != pending.otp_code:
        pending.attempts += 1
        db.commit()
        remaining = 5 - pending.attempts
        return templates.TemplateResponse(request, "verify_email.html", {
            "email": email,
            "error": f"Mã không đúng. Còn {remaining} lần thử.",
        }, status_code=400)

    # OTP correct → create account using the pre-hashed password
    pw_hash   = pending.password_hash
    full_name = pending.full_name
    db.delete(pending)
    db.flush()
    user = create_user(db, email=email, password="", full_name=full_name, _password_hash=pw_hash)

    request.session["user_id"] = user.id
    return RedirectResponse("/?success=Chao+mung+ban+den+voi+AutoBlogspot", status_code=303)


@router.post("/verify-email/resend")
def resend_otp(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote_plus
    email = email.strip().lower()
    pending = db.query(EmailVerification).filter(EmailVerification.email == email).first()

    if not pending:
        return RedirectResponse(f"/register?error={quote_plus('Phiên xác minh không tồn tại.')}", status_code=303)

    if pending.resend_count >= 5:
        return templates.TemplateResponse(request, "verify_email.html", {
            "email": email,
            "error": "Đã gửi lại quá nhiều lần. Vui lòng đăng ký lại.",
        }, status_code=429)

    # Generate new OTP and reset attempts
    new_otp = f"{secrets.randbelow(1_000_000):06d}"
    pending.otp_code     = new_otp
    pending.attempts     = 0
    pending.resend_count += 1
    pending.expires_at   = datetime.utcnow() + timedelta(minutes=15)
    db.commit()

    send_verification_email(email, new_otp, pending.full_name)
    return RedirectResponse(
        f"/verify-email?email={quote_plus(email)}&resent=1",
        status_code=303,
    )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {})


@router.post("/forgot-password")
def forgot_password(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_user_by_email(db, email)
    # Always show success to prevent email enumeration
    if user and user.is_active:
        token = generate_reset_token(db, user)
        reset_url = f"{settings.BASE_URL}/reset-password?token={token}"
        sent = send_password_reset(user.email, reset_url)
        if not sent:
            logger.warning("Could not send reset email to %s (email not configured)", user.email)
    return templates.TemplateResponse(request, "forgot_password.html", {
        "success": True,
    })


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    if not token:
        return RedirectResponse("/forgot-password")
    return templates.TemplateResponse(request, "reset_password.html", {"token": token})


@router.post("/reset-password")
def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if password != password_confirm:
        return templates.TemplateResponse(request, "reset_password.html", {
            "token": token, "error": "pw_mismatch",
        }, status_code=400)
    if len(password) < 6:
        return templates.TemplateResponse(request, "reset_password.html", {
            "token": token, "error": "pw_too_short",
        }, status_code=400)

    user = validate_reset_token(db, token)
    if not user:
        return templates.TemplateResponse(request, "reset_password.html", {
            "token": token, "error": "token_invalid",
        }, status_code=400)

    consume_reset_token(db, user, password)
    logger.info("Password reset for user %s", user.email)
    return templates.TemplateResponse(request, "reset_password.html", {
        "token": "", "done": True,
    })


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    from ..dependencies import get_current_user
    user = get_current_user(request, db)
    return templates.TemplateResponse(request, "profile.html", {
        "current_user": user,
        "active_page": "profile",
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@router.post("/profile")
def update_profile(
    request: Request,
    full_name: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote_plus
    from ..dependencies import get_current_user
    user = get_current_user(request, db)

    user.full_name = full_name.strip() or user.full_name

    if new_password:
        if not verify_password(current_password, user.password_hash):
            return RedirectResponse(f"/profile?error={quote_plus('Mật khẩu hiện tại không đúng')}", status_code=303)
        if len(new_password) < 6:
            return RedirectResponse(f"/profile?error={quote_plus('Mật khẩu mới phải có ít nhất 6 ký tự')}", status_code=303)
        if new_password != new_password_confirm:
            return RedirectResponse(f"/profile?error={quote_plus('Mật khẩu mới không khớp')}", status_code=303)
        user.password_hash = hash_password(new_password)
        logger.info("Password changed for user %s", user.email)

    db.commit()
    return RedirectResponse(f"/profile?success={quote_plus('Cập nhật thành công')}", status_code=303)
