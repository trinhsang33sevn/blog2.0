from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..services.auth_service import create_user, get_user_by_email, verify_password
from ..templates import templates

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
            "error": "Email hoặc mật khẩu không đúng"
        }, status_code=401)
    if not user.is_active:
        return templates.TemplateResponse(request, "login.html", {
            "error": "Tài khoản đã bị vô hiệu hóa"
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
            "error": "Mật khẩu xác nhận không khớp",
            "full_name": full_name, "email": email,
        }, status_code=400)

    if len(password) < 6:
        return templates.TemplateResponse(request, "register.html", {
            "error": "Mật khẩu phải có ít nhất 6 ký tự",
            "full_name": full_name, "email": email,
        }, status_code=400)

    if get_user_by_email(db, email):
        return templates.TemplateResponse(request, "register.html", {
            "error": "Email này đã được đăng ký",
            "full_name": full_name, "email": email,
        }, status_code=400)

    user = create_user(db, email=email, password=password, full_name=full_name)
    request.session["user_id"] = user.id
    return RedirectResponse("/?success=Chao+mung+ban+den+voi+AutoBlogspot", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ─── Admin routes ─────────────────────────────────────────────────────────────

@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    current = db.query(User).filter(User.id == user_id).first() if user_id else None
    if not current or not current.is_admin:
        return RedirectResponse("/")

    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin_users.html", {
        "users": users,
        "current_user": current,
        "active_page": "admin",
    })


@router.post("/admin/users/{uid}/upgrade")
def admin_upgrade(
    uid: int,
    request: Request,
    plan: str = Form(...),
    months: int = Form(1),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    current = db.query(User).filter(User.id == user_id).first() if user_id else None
    if not current or not current.is_admin:
        return RedirectResponse("/")

    from ..services.auth_service import upgrade_plan
    upgrade_plan(db, uid, plan, months)
    return RedirectResponse(f"/admin/users?success=Da+nang+cap+plan+{plan}", status_code=303)
