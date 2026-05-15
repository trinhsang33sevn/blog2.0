"""WordPress.com REST API v1.1 integration."""
import logging
from pathlib import Path
from urllib.parse import urlencode
import httpx
from sqlalchemy.orm import Session
from ..models import AppSetting

logger = logging.getLogger(__name__)

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".gif": "image/gif"}

WP_AUTH_URL  = "https://public-api.wordpress.com/oauth2/authorize"
WP_TOKEN_URL = "https://public-api.wordpress.com/oauth2/token"
WP_API_BASE  = "https://public-api.wordpress.com/rest/v1.1"


def _get_setting(db: Session, key: str, default: str = "", user_id: int = None) -> str:
    actual_key = f"u{user_id}_{key}" if user_id else key
    row = db.query(AppSetting).filter(AppSetting.key == actual_key).first()
    return row.value if row else default


def build_oauth_url(db: Session, redirect_uri: str, user_id: int = None) -> str:
    client_id = _get_setting(db, "wp_client_id", user_id=user_id)
    if not client_id:
        raise ValueError("WordPress.com Client ID chưa được cấu hình trong Cài đặt")
    params = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "global",
    }
    return f"{WP_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(db: Session, code: str, redirect_uri: str, user_id: int = None) -> dict:
    client_id     = _get_setting(db, "wp_client_id", user_id=user_id)
    client_secret = _get_setting(db, "wp_client_secret", user_id=user_id)
    with httpx.Client() as c:
        resp = c.post(WP_TOKEN_URL, data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  redirect_uri,
            "code":          code,
            "grant_type":    "authorization_code",
        })
        resp.raise_for_status()
        return resp.json()


def get_user_info(access_token: str) -> dict:
    with httpx.Client() as c:
        resp = c.get(f"{WP_API_BASE}/me", headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        return resp.json()


def get_user_sites(access_token: str) -> list[dict]:
    with httpx.Client(timeout=20) as c:
        resp = c.get(f"{WP_API_BASE}/me/sites", headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        data = resp.json()
    return [
        {"id": str(s["ID"]), "name": s.get("name", ""), "url": s.get("URL", "")}
        for s in data.get("sites", [])
    ]


def upload_media(access_token: str, site_id: str, file_path: Path) -> str | None:
    """Upload image to WordPress.com media library. Returns hosted URL or None."""
    mime = _MIME.get(file_path.suffix.lower(), "image/jpeg")
    try:
        with httpx.Client(timeout=60) as c:
            resp = c.post(
                f"{WP_API_BASE}/sites/{site_id}/media/new",
                headers={"Authorization": f"Bearer {access_token}"},
                files={"file": (file_path.name, file_path.read_bytes(), mime)},
            )
            resp.raise_for_status()
            media_list = resp.json().get("media", [])
            if media_list:
                return media_list[0].get("URL")
    except Exception as e:
        logger.warning("WP.com media upload failed: %s", e)
    return None


def publish_post(access_token: str, site_id: str, title: str, content: str,
                 tags: list | None = None) -> dict:
    body: dict = {
        "title":   title,
        "content": content,
        "status":  "publish",
        "format":  "standard",
    }
    if tags:
        body["tags"] = ",".join(tags)
    with httpx.Client(timeout=60) as c:
        resp = c.post(
            f"{WP_API_BASE}/sites/{site_id}/posts/new",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
    return {"url": data.get("URL", ""), "id": str(data.get("ID", ""))}


def update_post(access_token: str, site_id: str, post_id: str,
                title: str, content: str) -> dict:
    with httpx.Client(timeout=60) as c:
        resp = c.post(
            f"{WP_API_BASE}/sites/{site_id}/posts/{post_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"title": title, "content": content},
        )
        resp.raise_for_status()
        data = resp.json()
    return {"url": data.get("URL", ""), "id": str(data.get("ID", post_id))}
