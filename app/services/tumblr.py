"""Tumblr API v2 integration with OAuth2."""
from datetime import datetime, timedelta
from urllib.parse import urlencode
import httpx
from sqlalchemy.orm import Session
from ..models import AppSetting

TUMBLR_AUTH_URL  = "https://www.tumblr.com/oauth2/authorize"
TUMBLR_TOKEN_URL = "https://api.tumblr.com/v2/oauth2/token"
TUMBLR_API_BASE  = "https://api.tumblr.com/v2"


def _get_setting(db: Session, key: str, default: str = "", user_id: int = None) -> str:
    actual_key = f"u{user_id}_{key}" if user_id else key
    row = db.query(AppSetting).filter(AppSetting.key == actual_key).first()
    return row.value if row else default


def build_oauth_url(db: Session, redirect_uri: str, user_id: int = None) -> str:
    client_id = _get_setting(db, "tumblr_consumer_key", user_id=user_id)
    if not client_id:
        raise ValueError("Tumblr Consumer Key chưa được cấu hình trong Cài đặt")
    params = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "write offline_access",
    }
    return f"{TUMBLR_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(db: Session, code: str, redirect_uri: str, user_id: int = None) -> dict:
    client_id     = _get_setting(db, "tumblr_consumer_key", user_id=user_id)
    client_secret = _get_setting(db, "tumblr_consumer_secret", user_id=user_id)
    with httpx.Client() as c:
        resp = c.post(TUMBLR_TOKEN_URL,
            data={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
        )
        resp.raise_for_status()
        return resp.json()


def refresh_access_token(db: Session, platform_account) -> str:
    now = datetime.utcnow()
    if platform_account.token_expiry and platform_account.token_expiry > now + timedelta(minutes=5):
        return platform_account.access_token
    client_id     = _get_setting(db, "tumblr_consumer_key")
    client_secret = _get_setting(db, "tumblr_consumer_secret")
    with httpx.Client() as c:
        resp = c.post(TUMBLR_TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": platform_account.refresh_token,
            },
            auth=(client_id, client_secret),
        )
        resp.raise_for_status()
        data = resp.json()
    platform_account.access_token = data["access_token"]
    if "refresh_token" in data:
        platform_account.refresh_token = data["refresh_token"]
    platform_account.token_expiry = now + timedelta(seconds=data.get("expires_in", 3600))
    db.commit()
    return platform_account.access_token


def get_user_info(access_token: str) -> dict:
    with httpx.Client() as c:
        resp = c.get(
            f"{TUMBLR_API_BASE}/user/info",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()["response"]["user"]


def get_user_blogs(access_token: str) -> list[dict]:
    user = get_user_info(access_token)
    return [
        {
            "id":   b["name"],
            "name": b.get("title", b["name"]),
            "url":  b.get("url", f"https://{b['name']}.tumblr.com"),
        }
        for b in user.get("blogs", [])
    ]


def publish_post(access_token: str, blog_identifier: str, title: str, content: str,
                 tags: list | None = None) -> dict:
    """Đăng bài HTML lên Tumblr qua legacy text post endpoint (OAuth2 Bearer)."""
    body: dict = {
        "type":   "text",
        "state":  "published",
        "title":  title,
        "body":   content,
        "format": "html",
    }
    if tags:
        body["tags"] = ",".join(tags)
    with httpx.Client(timeout=60) as c:
        resp = c.post(
            f"{TUMBLR_API_BASE}/blog/{blog_identifier}/post",
            headers={"Authorization": f"Bearer {access_token}"},
            data=body,
        )
        resp.raise_for_status()
        data = resp.json()
    post_id  = str(data.get("response", {}).get("id", ""))
    host     = blog_identifier if "." in blog_identifier else f"{blog_identifier}.tumblr.com"
    post_url = f"https://{host}/post/{post_id}" if post_id else ""
    return {"url": post_url, "id": post_id}


def update_post(access_token: str, blog_identifier: str, post_id: str,
                title: str, content: str) -> dict:
    body: dict = {
        "id":     post_id,
        "type":   "text",
        "title":  title,
        "body":   content,
        "format": "html",
    }
    with httpx.Client(timeout=60) as c:
        resp = c.post(
            f"{TUMBLR_API_BASE}/blog/{blog_identifier}/post/edit",
            headers={"Authorization": f"Bearer {access_token}"},
            data=body,
        )
        resp.raise_for_status()
    host = blog_identifier if "." in blog_identifier else f"{blog_identifier}.tumblr.com"
    return {"url": f"https://{host}/post/{post_id}", "id": post_id}
