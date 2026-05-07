import logging

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..i18n import _ as t
from ..services.auth_service import (
    create_user, get_user_by_email, verify_password,
    generate_reset_token, validate_reset_token, consume_reset_token,
)
from ..services.email_service import send_password_reset
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

    user = create_user(db, email=email, password=password, full_name=full_name)
    request.session["user_id"] = user.id
    return RedirectResponse("/?success=Chao+mung+ban+den+voi+AutoBlogspot", status_code=303)


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
