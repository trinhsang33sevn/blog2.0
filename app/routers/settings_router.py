import json
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import get_current_user
from ..services.openrouter import (
    get_setting, set_setting, get_current_free_models, fetch_free_models_from_api,
)
from ..services.ai_providers import test_connection
from ..templates import templates

router = APIRouter()
_BASE = get_settings().BASE_URL.rstrip("/")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    uid = current_user.id
    models = get_current_free_models(db, user_id=uid)
    gemini_keys_raw = get_setting(db, "gemini_api_keys", user_id=uid)
    gemini_key_count = len([k for k in gemini_keys_raw.splitlines() if k.strip()]) if gemini_keys_raw else 0
    return templates.TemplateResponse(request, "settings.html", {
        "openrouter_api_key":     get_setting(db, "openrouter_api_key",   user_id=uid),
        "openrouter_model":       get_setting(db, "openrouter_model", "meta-llama/llama-3.3-70b-instruct:free", user_id=uid),
        "sinbyte_api_key":        get_setting(db, "sinbyte_api_key",      user_id=uid),
        "pixabay_api_key":        get_setting(db, "pixabay_api_key",      user_id=uid),
        "imgbb_api_key":          get_setting(db, "imgbb_api_key",         user_id=uid),
        "indexnow_key":           get_setting(db, "indexnow_key",          user_id=uid),
        "google_indexing_sa":     get_setting(db, "google_indexing_sa",    user_id=uid),
        "google_client_id":       get_setting(db, "google_client_id",     user_id=uid),
        "google_client_secret":   get_setting(db, "google_client_secret", user_id=uid),
        "wp_client_id":           get_setting(db, "wp_client_id",         user_id=uid),
        "wp_client_secret":       get_setting(db, "wp_client_secret",     user_id=uid),
        "tumblr_consumer_key":    get_setting(db, "tumblr_consumer_key",  user_id=uid),
        "tumblr_consumer_secret": get_setting(db, "tumblr_consumer_secret", user_id=uid),
        "gemini_api_keys":        gemini_keys_raw,
        "gemini_key_count":       gemini_key_count,
        "claude_api_key":         get_setting(db, "claude_api_key",       user_id=uid),
        "openai_api_key":         get_setting(db, "openai_api_key",       user_id=uid),
        "groq_api_key":           get_setting(db, "groq_api_key",         user_id=uid),
        "models":                 models,
        "models_count":           len(models),
        "models_cached":          bool(get_setting(db, "cached_free_models", user_id=uid)),
        "wp_configured":          bool(get_setting(db, "wp_client_id",        user_id=uid)),
        "tumblr_configured":      bool(get_setting(db, "tumblr_consumer_key", user_id=uid)),
        "google_configured":      bool(get_setting(db, "google_client_id",    user_id=uid)),
        "gemini_configured":      gemini_key_count > 0,
        "claude_configured":      bool(get_setting(db, "claude_api_key",      user_id=uid)),
        "openai_configured":      bool(get_setting(db, "openai_api_key",      user_id=uid)),
        "groq_configured":        bool(get_setting(db, "groq_api_key",        user_id=uid)),
        "current_user":           current_user,
        "active_page":            "settings",
        "base_url":               _BASE,
    })


@router.post("/settings")
def save_settings(
    request: Request,
    openrouter_api_key:      str = Form(""),
    openrouter_model:        str = Form(""),
    sinbyte_api_key:         str = Form(""),
    pixabay_api_key:         str = Form(""),
    imgbb_api_key:           str = Form(""),
    indexnow_key:            str = Form(""),
    google_indexing_sa:      str = Form(""),
    google_client_id:        str = Form(""),
    google_client_secret:    str = Form(""),
    wp_client_id:            str = Form(""),
    wp_client_secret:        str = Form(""),
    tumblr_consumer_key:     str = Form(""),
    tumblr_consumer_secret:  str = Form(""),
    gemini_api_keys:         str = Form(""),
    claude_api_key:          str = Form(""),
    openai_api_key:          str = Form(""),
    groq_api_key:            str = Form(""),
    active_tab:              str = Form("ai"),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    uid = current_user.id
    # Luôn gọi set_setting kể cả khi empty — set_setting() sẽ xóa key nếu value rỗng
    for key, val in [
        ("openrouter_api_key",    openrouter_api_key.strip()),
        ("openrouter_model",      openrouter_model.strip()),
        ("sinbyte_api_key",       sinbyte_api_key.strip()),
        ("pixabay_api_key",       pixabay_api_key.strip()),
        ("imgbb_api_key",         imgbb_api_key.strip()),
        ("indexnow_key",          indexnow_key.strip()),
        ("google_indexing_sa",    google_indexing_sa.strip()),
        ("google_client_id",      google_client_id.strip()),
        ("google_client_secret",  google_client_secret.strip()),
        ("wp_client_id",          wp_client_id.strip()),
        ("wp_client_secret",      wp_client_secret.strip()),
        ("tumblr_consumer_key",   tumblr_consumer_key.strip()),
        ("tumblr_consumer_secret", tumblr_consumer_secret.strip()),
        ("gemini_api_keys",       "\n".join(k.strip() for k in gemini_api_keys.splitlines() if k.strip())),
        ("claude_api_key",        claude_api_key.strip()),
        ("openai_api_key",        openai_api_key.strip()),
        ("groq_api_key",          groq_api_key.strip()),
    ]:
        set_setting(db, key, val, user_id=uid)
    tab = active_tab.strip() or "ai"
    return RedirectResponse(f"/settings?success=Da+luu+cai+dat&tab={tab}", status_code=303)


@router.post("/settings/refresh-models")
def refresh_models(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    uid = current_user.id
    try:
        api_key = get_setting(db, "openrouter_api_key", user_id=uid)
        if not api_key:
            return RedirectResponse("/settings?error=Chua+co+OpenRouter+API+key&tab=ai", status_code=303)
        models = fetch_free_models_from_api(api_key)
        set_setting(db, "cached_free_models", json.dumps(models), user_id=uid)
        return RedirectResponse(
            f"/settings?success=Da+cap+nhat+{len(models)}+free+models&tab=ai",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(f"/settings?error={str(e)}&tab=ai", status_code=303)


@router.post("/settings/change-password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password:     str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    from ..services.auth_service import verify_password, hash_password
    current_user = get_current_user(request, db)

    if not verify_password(current_password, current_user.password_hash):
        return JSONResponse({"ok": False, "message": "Mật khẩu hiện tại không đúng"}, status_code=400)
    if new_password != confirm_password:
        return JSONResponse({"ok": False, "message": "Mật khẩu xác nhận không khớp"}, status_code=400)
    if len(new_password) < 6:
        return JSONResponse({"ok": False, "message": "Mật khẩu mới phải có ít nhất 6 ký tự"}, status_code=400)

    current_user.password_hash = hash_password(new_password)
    db.commit()
    return JSONResponse({"ok": True, "message": "Đổi mật khẩu thành công"})


@router.post("/settings/test-api/{provider}")
def test_api_connection(
    provider: str,
    request: Request,
    api_key: str = Form(...),
    db: Session = Depends(get_db),
):
    get_current_user(request, db)
    result = test_connection(provider, api_key)
    return JSONResponse(result)
