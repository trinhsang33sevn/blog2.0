import os
import time
from datetime import datetime, timedelta

import psutil
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db, engine
from ..models import User, Subscription, Article
from ..services.openrouter import set_setting, get_setting
from ..services.auth_service import upgrade_plan
from ..templates import templates, update_site_globals

router = APIRouter()

_START_TIME = time.time()

PLAN_PRICES = {"free": 0, "pro": 200_000, "business": 500_000, "gift": 0}


def _require_admin(request: Request, db: Session):
    user_id = request.session.get("user_id")
    user = db.query(User).filter(User.id == user_id, User.is_admin == True).first() if user_id else None
    return user


def _user_stats(db: Session) -> dict:
    total = db.query(User).count()
    by_plan = {p: 0 for p in ("free", "pro", "business", "gift")}
    active_count = 0
    expired_count = 0
    trial_count = 0
    gift_count = 0
    now = datetime.utcnow()
    month_ago = now - timedelta(days=30)
    new_this_month = db.query(User).filter(User.created_at >= month_ago).count()

    subs = db.query(Subscription).all()
    for s in subs:
        plan = s.plan if s.plan in by_plan else "free"
        by_plan[plan] += 1
        if s.is_active_plan:
            if s.status == "trial":
                trial_count += 1
            elif s.status == "gift":
                gift_count += 1
            else:
                active_count += 1
        else:
            expired_count += 1

    return {
        "total": total,
        "by_plan": by_plan,
        "active": active_count,
        "trial": trial_count,
        "gift": gift_count,
        "expired": expired_count,
        "new_this_month": new_this_month,
    }


def _revenue_stats(db: Session) -> dict:
    now = datetime.utcnow()
    mrr = 0
    subs = db.query(Subscription).filter(Subscription.status.notin_(["trial", "gift"])).all()
    for s in subs:
        if s.is_active_plan and s.plan in PLAN_PRICES:
            mrr += PLAN_PRICES[s.plan]

    total_published = db.query(Article).filter(Article.status == "published").count()
    total_failed    = db.query(Article).filter(Article.status == "failed").count()
    today = now.date()
    published_today = db.query(Article).filter(
        Article.status == "published",
        func.date(Article.published_at) == today,
    ).count()

    return {
        "mrr": mrr,
        "total_published": total_published,
        "total_failed": total_failed,
        "published_today": published_today,
    }


def _system_stats(db: Session) -> dict:
    cpu    = psutil.cpu_percent(interval=0.2)
    mem    = psutil.virtual_memory()
    disk   = psutil.disk_usage(".")
    uptime = int(time.time() - _START_TIME)
    h, rem = divmod(uptime, 3600)
    m, s   = divmod(rem, 60)

    try:
        db_path = str(engine.url).replace("sqlite:///", "")
        db_size_mb = round(os.path.getsize(db_path) / 1024 / 1024, 2)
    except OSError:
        db_size_mb = 0  # PostgreSQL or path not accessible

    import platform, sys
    return {
        "cpu_pct":    cpu,
        "mem_pct":    mem.percent,
        "mem_used_gb": round(mem.used / 1024**3, 1),
        "mem_total_gb": round(mem.total / 1024**3, 1),
        "disk_pct":   disk.percent,
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "uptime":     f"{h}h {m}m {s}s",
        "db_size_mb": db_size_mb,
        "python_ver": sys.version.split()[0],
        "os_info":    platform.platform(terse=True),
        "cpu_cores":  psutil.cpu_count(),
    }


def _payment_config(db: Session) -> dict:
    return {
        "payment_bank_name":      get_setting(db, "payment_bank_name"),
        "payment_account_number": get_setting(db, "payment_account_number"),
        "payment_account_holder": get_setting(db, "payment_account_holder"),
        "payment_transfer_note":  get_setting(db, "payment_transfer_note"),
        "sepay_api_key":          get_setting(db, "sepay_api_key"),
        "ls_api_key":             get_setting(db, "ls_api_key"),
        "ls_webhook_secret":      get_setting(db, "ls_webhook_secret"),
        "ls_store_id":            get_setting(db, "ls_store_id"),
        "ls_pro_variant_id":      get_setting(db, "ls_pro_variant_id"),
        "ls_business_variant_id": get_setting(db, "ls_business_variant_id"),
        "ls_pro_price":           get_setting(db, "ls_pro_price") or "$8/month",
        "ls_business_price":      get_setting(db, "ls_business_price") or "$20/month",
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, tab: str = "overview", db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/")

    users = db.query(User).order_by(User.created_at.desc()).all()

    return templates.TemplateResponse(request, "admin.html", {
        "current_user":      admin,
        "active_page":       "admin",
        "tab":               tab,
        "users":             users,
        "user_stats":        _user_stats(db),
        "revenue_stats":     _revenue_stats(db),
        "system_stats":      _system_stats(db),
        "payment_config":    _payment_config(db),
        "plan_prices":       PLAN_PRICES,
        "telegram_username": get_setting(db, "telegram_username"),
        "contact_email":     get_setting(db, "contact_email") or "hoangvandonglx@gmail.com",
    })


@router.post("/admin/contact-config")
def save_contact_config(
    request: Request,
    telegram_username: str = Form(""),
    contact_email:     str = Form(""),
    db: Session = Depends(get_db),
):
    if not _require_admin(request, db):
        return RedirectResponse("/")
    tg = telegram_username.strip().lstrip("@")
    set_setting(db, "telegram_username", tg)
    set_setting(db, "contact_email", contact_email.strip())
    update_site_globals(TELEGRAM_USERNAME=tg)
    return RedirectResponse("/admin?tab=contact&success=Da+luu+thong+tin+lien+he", status_code=303)


@router.post("/admin/users/{uid}/upgrade")
def admin_upgrade(
    uid: int, request: Request,
    plan: str = Form(...), months: int = Form(1),
    db: Session = Depends(get_db),
):
    if not _require_admin(request, db):
        return RedirectResponse("/")
    upgrade_plan(db, uid, plan, months)
    return RedirectResponse("/admin?tab=members&success=Da+nang+cap+plan", status_code=303)


@router.post("/admin/payment-config")
def save_payment_config(
    request: Request,
    payment_bank_name:      str = Form(""),
    payment_account_number: str = Form(""),
    payment_account_holder: str = Form(""),
    payment_transfer_note:  str = Form(""),
    sepay_api_key:          str = Form(""),
    db: Session = Depends(get_db),
):
    if not _require_admin(request, db):
        return RedirectResponse("/")
    for key, val in [
        ("payment_bank_name",      payment_bank_name),
        ("payment_account_number", payment_account_number),
        ("payment_account_holder", payment_account_holder),
        ("payment_transfer_note",  payment_transfer_note),
        ("sepay_api_key",          sepay_api_key),
    ]:
        set_setting(db, key, val.strip())
    return RedirectResponse("/admin?tab=revenue&success=Da+luu+thong+tin+thanh+toan", status_code=303)


@router.post("/admin/lemonsqueezy-config")
def save_lemonsqueezy_config(
    request: Request,
    ls_api_key:             str = Form(""),
    ls_webhook_secret:      str = Form(""),
    ls_store_id:            str = Form(""),
    ls_pro_variant_id:      str = Form(""),
    ls_business_variant_id: str = Form(""),
    ls_pro_price:           str = Form(""),
    ls_business_price:      str = Form(""),
    db: Session = Depends(get_db),
):
    if not _require_admin(request, db):
        return RedirectResponse("/")
    for key, val in [
        ("ls_api_key",             ls_api_key),
        ("ls_webhook_secret",      ls_webhook_secret),
        ("ls_store_id",            ls_store_id),
        ("ls_pro_variant_id",      ls_pro_variant_id),
        ("ls_business_variant_id", ls_business_variant_id),
        ("ls_pro_price",           ls_pro_price),
        ("ls_business_price",      ls_business_price),
    ]:
        set_setting(db, key, val.strip())
    return RedirectResponse("/admin?tab=revenue&success=Da+luu+LemonSqueezy+config", status_code=303)


@router.post("/admin/users/{uid}/toggle-active")
def toggle_user_active(uid: int, request: Request, db: Session = Depends(get_db)):
    if not _require_admin(request, db):
        return RedirectResponse("/")
    user = db.query(User).filter(User.id == uid).first()
    if user:
        user.is_active = not user.is_active
        db.commit()
    return RedirectResponse("/admin?tab=members", status_code=303)


@router.post("/admin/users/{uid}/delete")
def delete_user(uid: int, request: Request, db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/")
    if admin.id == uid:
        return RedirectResponse("/admin?tab=members&error=Khong+the+xoa+chinh+minh", status_code=303)
    user = db.query(User).filter(User.id == uid).first()
    if user:
        db.delete(user)
        db.commit()
    return RedirectResponse("/admin?tab=members&success=Da+xoa+nguoi+dung", status_code=303)
