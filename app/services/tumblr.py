"""Tumblr API v2 integration with OAuth2."""
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from sqlalchemy.orm import Session
from ..models import AppSetting

TUMBLR_AUTH_URL  = "https://www.tumblr.com/oauth2/authorize"
TUMBLR_TOKEN_URL = "https://api.tumblr.com/v2/oauth2/token"
TUMBLR_API_BASE  = "https://api.tumblr.com/v2"

_TIMEOUT = 60


def _get_setting(db: Session, key: str, default: str = "", user_id: int = None) -> str:
    actual_key = f"u{user_id}_{key}" if user_id else key
    row = db.query(AppSetting).filter(AppSetting.key == actual_key).first()
    return row.value if row else default


def _post_form(url: str, data: dict, headers: dict = None) -> dict:
    body = urlencode(data).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        raise RuntimeError(f"Tumblr API {e.code}: {e.read(300).decode(errors='replace')}") from e


def _get_json(url: str, access_token: str) -> dict:
    req = Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        raise RuntimeError(f"Tumblr API {e.code}: {e.read(300).decode(errors='replace')}") from e


def build_oauth_url(db: Session, redirect_uri: str, user_id: int = None, state: str = None) -> str:
    import secrets
    client_id = _get_setting(db, "tumblr_consumer_key", user_id=user_id)
    if not client_id:
        raise ValueError("Tumblr Consumer Key chưa được cấu hình trong Cài đặt")
    params = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "write offline_access",
        "state":         state or secrets.token_urlsafe(16),
    }
    return f"{TUMBLR_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(db: Session, code: str, redirect_uri: str, user_id: int = None) -> dict:
    client_id     = _get_setting(db, "tumblr_consumer_key", user_id=user_id)
    client_secret = _get_setting(db, "tumblr_consumer_secret", user_id=user_id)
    return _post_form(TUMBLR_TOKEN_URL, {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "client_id":     client_id,
        "client_secret": client_secret,
    })


def refresh_access_token(db: Session, platform_account) -> str:
    now = datetime.utcnow()
    if platform_account.token_expiry and platform_account.token_expiry > now + timedelta(minutes=5):
        return platform_account.access_token
    user_id       = getattr(platform_account, "user_id", None)
    client_id     = _get_setting(db, "tumblr_consumer_key",    user_id=user_id)
    client_secret = _get_setting(db, "tumblr_consumer_secret", user_id=user_id)
    data = _post_form(TUMBLR_TOKEN_URL, {
        "grant_type":    "refresh_token",
        "refresh_token": platform_account.refresh_token,
        "client_id":     client_id,
        "client_secret": client_secret,
    })
    platform_account.access_token = data["access_token"]
    if "refresh_token" in data:
        platform_account.refresh_token = data["refresh_token"]
    platform_account.token_expiry = now + timedelta(seconds=data.get("expires_in", 3600))
    db.commit()
    return platform_account.access_token


def get_user_info(access_token: str) -> dict:
    data = _get_json(f"{TUMBLR_API_BASE}/user/info", access_token)
    return data["response"]["user"]


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
    data = _post_form(
        f"{TUMBLR_API_BASE}/blog/{blog_identifier}/post",
        body,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    post_id  = str(data.get("response", {}).get("id", ""))
    host     = blog_identifier if "." in blog_identifier else f"{blog_identifier}.tumblr.com"
    post_url = f"https://{host}/post/{post_id}" if post_id else ""
    return {"url": post_url, "id": post_id}


def update_post(access_token: str, blog_identifier: str, post_id: str,
                title: str, content: str) -> dict:
    _post_form(
        f"{TUMBLR_API_BASE}/blog/{blog_identifier}/post/edit",
        {"id": post_id, "type": "text", "title": title, "body": content, "format": "html"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    host = blog_identifier if "." in blog_identifier else f"{blog_identifier}.tumblr.com"
    return {"url": f"https://{host}/post/{post_id}", "id": post_id}
