from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import get_current_user, can_add_site
from ..models import GoogleAccount, BlogspotSite, PlatformAccount
from ..services import blogger, wordpress, tumblr, hashnode, wordpress_selfhosted as wp_sh
from ..templates import templates

router = APIRouter()

_BASE = get_settings().BASE_URL.rstrip("/")
_BLOGGER_REDIRECT = f"{_BASE}/accounts/oauth/callback"
_WP_REDIRECT      = f"{_BASE}/accounts/wordpress/callback"
_TUMBLR_REDIRECT  = f"{_BASE}/accounts/tumblr/callback"

LANGUAGES = [
    ("vi", "Tiếng Việt"), ("en", "Tiếng Anh"), ("de", "Tiếng Đức"),
    ("fr", "Tiếng Pháp"), ("ko", "Tiếng Hàn"), ("ja", "Tiếng Nhật"),
    ("es", "Tiếng Tây Ban Nha"),
]


# ─── Accounts page ────────────────────────────────────────────────────────────

@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    uid = current_user.id
    google_accounts        = db.query(GoogleAccount).filter(GoogleAccount.user_id == uid).all()
    wp_accounts            = db.query(PlatformAccount).filter(PlatformAccount.user_id == uid, PlatformAccount.platform == "wordpress").all()
    tumblr_accounts        = db.query(PlatformAccount).filter(PlatformAccount.user_id == uid, PlatformAccount.platform == "tumblr").all()
    hashnode_accounts      = db.query(PlatformAccount).filter(PlatformAccount.user_id == uid, PlatformAccount.platform == "hashnode").all()
    wp_selfhosted_accounts = db.query(PlatformAccount).filter(PlatformAccount.user_id == uid, PlatformAccount.platform == "wordpress_selfhosted").all()

    from ..services.openrouter import get_setting as _gs
    return templates.TemplateResponse(request, "accounts.html", {
        "accounts":               google_accounts,
        "wp_accounts":            wp_accounts,
        "tumblr_accounts":        tumblr_accounts,
        "hashnode_accounts":      hashnode_accounts,
        "wp_selfhosted_accounts": wp_selfhosted_accounts,
        "wp_configured":          bool(_gs(db, "wp_client_id", user_id=uid)),
        "tumblr_configured":      bool(_gs(db, "tumblr_consumer_key", user_id=uid)),
        "google_configured":      bool(_gs(db, "google_client_id", user_id=uid)),
        "languages":              LANGUAGES,
        "current_user":           current_user,
        "active_page":            "accounts",
        "base_url":               _BASE,
    })


# ─── Blogspot / Google ────────────────────────────────────────────────────────

@router.get("/accounts/oauth/start")
def oauth_start(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    ok, msg = can_add_site(current_user, db)
    if not ok:
        return RedirectResponse(f"/accounts?error={msg}")
    try:
        url = blogger.build_oauth_url(db, _BLOGGER_REDIRECT, user_id=current_user.id)
        return RedirectResponse(url)
    except ValueError as e:
        from urllib.parse import quote_plus
        return RedirectResponse(f"/accounts?error={quote_plus(str(e))}")


@router.get("/accounts/oauth/callback")
def oauth_callback(request: Request, code: str = None, error: str = None, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if error or not code:
        return RedirectResponse("/accounts?error=OAuth+cancelled+or+failed")
    try:
        tokens        = blogger.exchange_code_for_tokens(db, code, _BLOGGER_REDIRECT, user_id=current_user.id)
        access_token  = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", "")
        expires_in    = tokens.get("expires_in", 3600)
        user_info     = blogger.get_user_info(access_token)
        email, name   = user_info.get("email", ""), user_info.get("name", "")

        # Lookup by email only (không filter user_id) để tránh UNIQUE violation
        # khi account đã tồn tại từ trước multi-tenancy hoặc được re-connect
        existing = db.query(GoogleAccount).filter(GoogleAccount.email == email).first()
        if existing:
            existing.user_id      = current_user.id
            existing.name         = name
            existing.access_token = access_token
            if refresh_token:
                existing.refresh_token = refresh_token
            existing.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
        else:
            db.add(GoogleAccount(
                user_id=current_user.id, name=name, email=email,
                access_token=access_token, refresh_token=refresh_token,
                token_expiry=datetime.utcnow() + timedelta(seconds=expires_in),
            ))
        db.commit()
        return RedirectResponse("/accounts?success=Google+account+connected")
    except Exception as e:
        return RedirectResponse(f"/accounts?error={str(e)}")


@router.post("/accounts/{account_id}/sync-blogs")
def sync_blogs(account_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    account = db.query(GoogleAccount).filter(
        GoogleAccount.id == account_id, GoogleAccount.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404)
    try:
        blogs, synced = blogger.get_user_blogs(db, account), 0
        for blog in blogs:
            ok, _ = can_add_site(current_user, db)
            if not ok:
                break
            if not db.query(BlogspotSite).filter(
                BlogspotSite.blog_id == blog["id"], BlogspotSite.account_id == account_id
            ).first():
                db.add(BlogspotSite(
                    user_id=current_user.id, account_id=account_id, platform="blogspot",
                    blog_id=blog["id"], blog_url=blog.get("url", ""), blog_name=blog.get("name", ""),
                ))
                synced += 1
        db.commit()
        return RedirectResponse(f"/accounts?success=Synced+{synced}+new+Blogspot+sites", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/accounts?error={str(e)}", status_code=303)


@router.post("/accounts/{account_id}/add-blog")
def add_blog_manually(
    account_id: int, request: Request,
    blog_url: str = Form(""), blog_name: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    ok, msg = can_add_site(current_user, db)
    if not ok:
        return RedirectResponse(f"/accounts?error={msg}", status_code=303)
    account = db.query(GoogleAccount).filter(
        GoogleAccount.id == account_id, GoogleAccount.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404)
    try:
        token   = blogger.refresh_access_token(db, account)
        info    = blogger.get_blog_by_url(token, blog_url)
        blog_id = info["id"]
    except Exception as e:
        return RedirectResponse(f"/accounts?error=Khong+lay+duoc+blog:+{str(e)}", status_code=303)

    if not db.query(BlogspotSite).filter(
        BlogspotSite.blog_id == blog_id, BlogspotSite.account_id == account_id
    ).first():
        db.add(BlogspotSite(
            user_id=current_user.id, account_id=account_id, platform="blogspot",
            blog_id=blog_id, blog_url=info["url"], blog_name=blog_name.strip() or info["name"],
        ))
        db.commit()
    return RedirectResponse("/accounts?success=Blog+added+successfully", status_code=303)


@router.post("/accounts/{account_id}/delete")
def delete_google_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    account = db.query(GoogleAccount).filter(
        GoogleAccount.id == account_id, GoogleAccount.user_id == current_user.id
    ).first()
    if account:
        db.delete(account)
        db.commit()
    return RedirectResponse("/accounts?success=Account+deleted", status_code=303)


# ─── Sites shared ─────────────────────────────────────────────────────────────

@router.post("/accounts/sites/{site_id}/update")
def update_site(
    site_id: int, request: Request,
    category: str = Form(""), default_language: str = Form("vi"), is_active: str = Form("on"),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    site = db.query(BlogspotSite).filter(
        BlogspotSite.id == site_id, BlogspotSite.user_id == current_user.id
    ).first()
    if not site:
        raise HTTPException(status_code=404)
    site.category         = category
    site.default_language = default_language
    site.is_active        = is_active == "on"
    db.commit()
    platform = site.platform or "blogspot"
    tab = {"wordpress": "wordpress", "tumblr": "tumblr", "hashnode": "hashnode",
           "wordpress_selfhosted": "wpselfhosted"}.get(platform, "blogspot")
    return RedirectResponse(f"/accounts?success=Site+updated&tab={tab}", status_code=303)


# ─── WordPress.com ────────────────────────────────────────────────────────────

@router.get("/accounts/wordpress/start")
def wp_oauth_start(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    try:
        url = wordpress.build_oauth_url(db, _WP_REDIRECT, user_id=current_user.id)
        return RedirectResponse(url)
    except ValueError as e:
        from urllib.parse import quote_plus
        return RedirectResponse(f"/accounts?tab=wordpress&error={quote_plus(str(e))}")


@router.get("/accounts/wordpress/callback")
def wp_oauth_callback(request: Request, code: str = None, error: str = None, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if error or not code:
        return RedirectResponse("/accounts?error=WordPress+OAuth+cancelled")
    try:
        tokens       = wordpress.exchange_code_for_tokens(db, code, _WP_REDIRECT, user_id=current_user.id)
        access_token = tokens["access_token"]
        info         = wordpress.get_user_info(access_token)
        display_name = info.get("display_name") or info.get("username") or "WordPress"

        existing = db.query(PlatformAccount).filter(
            PlatformAccount.user_id == current_user.id,
            PlatformAccount.platform == "wordpress",
            PlatformAccount.name == display_name,
        ).first()
        if existing:
            existing.access_token = access_token
        else:
            db.add(PlatformAccount(
                user_id=current_user.id, platform="wordpress",
                name=display_name, access_token=access_token,
            ))
        db.commit()
        return RedirectResponse("/accounts?success=WordPress.com+connected&tab=wordpress")
    except Exception as e:
        return RedirectResponse(f"/accounts?error={str(e)}")


@router.post("/accounts/wordpress/{account_id}/sync-sites")
def wp_sync_sites(account_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    pa = db.query(PlatformAccount).filter(
        PlatformAccount.id == account_id, PlatformAccount.user_id == current_user.id,
        PlatformAccount.platform == "wordpress",
    ).first()
    if not pa:
        raise HTTPException(status_code=404)
    try:
        sites  = wordpress.get_user_sites(pa.access_token)
        synced = 0
        for s in sites:
            ok, _ = can_add_site(current_user, db)
            if not ok:
                break
            if not db.query(BlogspotSite).filter(
                BlogspotSite.blog_id == s["id"], BlogspotSite.platform_account_id == account_id
            ).first():
                db.add(BlogspotSite(
                    user_id=current_user.id, platform="wordpress",
                    platform_account_id=account_id,
                    blog_id=s["id"], blog_url=s["url"], blog_name=s["name"],
                ))
                synced += 1
        db.commit()
        return RedirectResponse(f"/accounts?success=Synced+{synced}+WordPress+sites&tab=wordpress", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/accounts?error={str(e)}", status_code=303)


# ─── Tumblr ───────────────────────────────────────────────────────────────────

@router.get("/accounts/tumblr/start")
def tumblr_oauth_start(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    try:
        import secrets
        state = secrets.token_urlsafe(16)
        request.session["tumblr_oauth_state"] = state
        url = tumblr.build_oauth_url(db, _TUMBLR_REDIRECT, user_id=current_user.id, state=state)
        return RedirectResponse(url)
    except ValueError as e:
        from urllib.parse import quote_plus
        return RedirectResponse(f"/accounts?tab=tumblr&error={quote_plus(str(e))}")


@router.get("/accounts/tumblr/callback")
def tumblr_oauth_callback(
    request: Request,
    code: str = None, error: str = None, state: str = None,
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    from urllib.parse import quote_plus

    if error or not code:
        _ERROR_HINTS = {
            "access_denied":         "Bạn đã huỷ xác thực.",
            "redirect_uri_mismatch": "Redirect URI không khớp — kiểm tra 'OAuth2 redirect URLs' trong Tumblr app.",
            "invalid_client":        "Consumer Key/Secret không hợp lệ — kiểm tra lại trong Cài đặt.",
            "unauthorized_client":   "App chưa được phê duyệt scope 'write offline_access'.",
            "invalid_request":       "Thiếu tham số bắt buộc — thử kết nối lại từ đầu.",
        }
        hint = _ERROR_HINTS.get(error or "", f"Lỗi OAuth: {error or 'không có mã'}")
        return RedirectResponse(f"/accounts?tab=tumblr&error={quote_plus(hint)}")

    expected = request.session.pop("tumblr_oauth_state", None)
    if expected and state != expected:
        return RedirectResponse(f"/accounts?tab=tumblr&error={quote_plus('State không hợp lệ — thử kết nối lại.')}")
    try:
        tokens        = tumblr.exchange_code_for_tokens(db, code, _TUMBLR_REDIRECT, user_id=current_user.id)
        access_token  = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", "")
        expires_in    = tokens.get("expires_in", 3600)
        info          = tumblr.get_user_info(access_token)
        name          = info.get("name", "Tumblr")

        existing = db.query(PlatformAccount).filter(
            PlatformAccount.user_id == current_user.id,
            PlatformAccount.platform == "tumblr",
            PlatformAccount.name == name,
        ).first()
        if existing:
            existing.access_token  = access_token
            existing.refresh_token = refresh_token
            existing.token_expiry  = datetime.utcnow() + timedelta(seconds=expires_in)
        else:
            db.add(PlatformAccount(
                user_id=current_user.id, platform="tumblr", name=name,
                access_token=access_token, refresh_token=refresh_token,
                token_expiry=datetime.utcnow() + timedelta(seconds=expires_in),
            ))
        db.commit()
        return RedirectResponse("/accounts?success=Tumblr+connected&tab=tumblr")
    except Exception as e:
        return RedirectResponse(f"/accounts?error={str(e)}")


@router.post("/accounts/tumblr/{account_id}/sync-blogs")
def tumblr_sync_blogs(account_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    pa = db.query(PlatformAccount).filter(
        PlatformAccount.id == account_id, PlatformAccount.user_id == current_user.id,
        PlatformAccount.platform == "tumblr",
    ).first()
    if not pa:
        raise HTTPException(status_code=404)
    try:
        token  = tumblr.refresh_access_token(db, pa)
        blogs  = tumblr.get_user_blogs(token)
        synced = 0
        for b in blogs:
            ok, _ = can_add_site(current_user, db)
            if not ok:
                break
            if not db.query(BlogspotSite).filter(
                BlogspotSite.blog_id == b["id"], BlogspotSite.platform_account_id == account_id
            ).first():
                db.add(BlogspotSite(
                    user_id=current_user.id, platform="tumblr",
                    platform_account_id=account_id,
                    blog_id=b["id"], blog_url=b["url"], blog_name=b["name"],
                ))
                synced += 1
        db.commit()
        return RedirectResponse(f"/accounts?success=Synced+{synced}+Tumblr+blogs&tab=tumblr", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/accounts?error={str(e)}", status_code=303)


# ─── Hashnode ─────────────────────────────────────────────────────────────────

@router.post("/accounts/hashnode/connect")
def hashnode_connect(
    request: Request,
    api_key: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    try:
        pubs = hashnode.get_user_publications(api_key)
        if not pubs:
            return RedirectResponse("/accounts?error=Khong+tim+thay+publication+nao+tren+Hashnode&tab=hashnode", status_code=303)

        existing = db.query(PlatformAccount).filter(
            PlatformAccount.user_id == current_user.id,
            PlatformAccount.platform == "hashnode",
            PlatformAccount.access_token == api_key,
        ).first()
        if not existing:
            pa = PlatformAccount(
                user_id=current_user.id, platform="hashnode",
                name=pubs[0]["name"], access_token=api_key,
            )
            db.add(pa)
            db.flush()
        else:
            pa = existing

        synced = 0
        for pub in pubs:
            ok, _ = can_add_site(current_user, db)
            if not ok:
                break
            if not db.query(BlogspotSite).filter(
                BlogspotSite.blog_id == pub["id"], BlogspotSite.platform_account_id == pa.id
            ).first():
                db.add(BlogspotSite(
                    user_id=current_user.id, platform="hashnode",
                    platform_account_id=pa.id,
                    blog_id=pub["id"], blog_url=pub["url"], blog_name=pub["name"],
                ))
                synced += 1
        db.commit()
        return RedirectResponse(f"/accounts?success=Hashnode+connected+{synced}+publications&tab=hashnode", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/accounts?error={str(e)}", status_code=303)


# ─── WordPress Self-hosted ───────────────────────────────────────────────────

@router.post("/accounts/wpselfhosted/connect")
def wp_selfhosted_connect(
    request: Request,
    site_url: str = Form(...),
    username: str = Form(...),
    app_password: str = Form(...),
    label: str = Form(""),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote_plus
    current_user = get_current_user(request, db)
    ok, msg = can_add_site(current_user, db)
    if not ok:
        return RedirectResponse(f"/accounts?error={quote_plus(msg)}&tab=wpselfhosted", status_code=303)

    site_url = site_url.strip().rstrip("/")
    if not site_url.startswith("http"):
        site_url = "https://" + site_url
    username = username.strip()
    app_password = app_password.strip()

    try:
        wp_sh.test_connection(site_url, username, app_password)
    except ValueError as e:
        return RedirectResponse(f"/accounts?error={quote_plus(str(e))}&tab=wpselfhosted", status_code=303)

    display_name = label.strip() or f"{username}@{site_url.split('//')[-1]}"

    existing = db.query(PlatformAccount).filter(
        PlatformAccount.user_id == current_user.id,
        PlatformAccount.platform == "wordpress_selfhosted",
        PlatformAccount.refresh_token == site_url,
        PlatformAccount.name == username,
    ).first()

    if existing:
        existing.access_token = app_password
        db.commit()
        return RedirectResponse(f"/accounts?success=WordPress+Self-hosted+updated&tab=wpselfhosted", status_code=303)

    pa = PlatformAccount(
        user_id=current_user.id,
        platform="wordpress_selfhosted",
        name=username,
        access_token=app_password,
        refresh_token=site_url,
    )
    db.add(pa)
    db.flush()

    db.add(BlogspotSite(
        user_id=current_user.id,
        platform="wordpress_selfhosted",
        platform_account_id=pa.id,
        blog_id=site_url,
        blog_url=site_url,
        blog_name=display_name,
    ))
    db.commit()
    return RedirectResponse(f"/accounts?success=WordPress+Self-hosted+connected&tab=wpselfhosted", status_code=303)


# ─── Delete platform account ──────────────────────────────────────────────────

@router.post("/accounts/platform/{account_id}/delete")
def delete_platform_account(account_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    pa = db.query(PlatformAccount).filter(
        PlatformAccount.id == account_id, PlatformAccount.user_id == current_user.id
    ).first()
    tab = "blogspot"
    if pa:
        tab = {"wordpress": "wordpress", "tumblr": "tumblr", "hashnode": "hashnode",
               "wordpress_selfhosted": "wpselfhosted"}.get(pa.platform or "", "blogspot")
        db.query(BlogspotSite).filter(BlogspotSite.platform_account_id == account_id).delete()
        db.delete(pa)
        db.commit()
    return RedirectResponse(f"/accounts?success=Account+deleted&tab={tab}", status_code=303)
