"""
Kết nối Blogspot qua Blogger API v3 (fallback sang GData/AtomPub nếu cần).
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode
import httpx
from sqlalchemy.orm import Session
from ..models import AppSetting, GoogleAccount

# ─── Constants ────────────────────────────────────────────────────────────────

BLOGGER_FEEDS_BASE = "https://www.blogger.com/feeds"
BLOGGER_API_V3_BASE = "https://www.googleapis.com/blogger/v3"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

BLOGGER_SCOPES = [
    "https://www.googleapis.com/auth/blogger",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

ATOM_NS = "http://www.w3.org/2005/Atom"
APP_NS = "http://www.w3.org/2007/app"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_setting(db: Session, key: str, default: str = "", user_id=None) -> str:
    if user_id:
        row = db.query(AppSetting).filter(AppSetting.key == f"u{user_id}_{key}").first()
        return row.value if row else default
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


def _atom_entry(title: str, content: str, labels: Optional[list[str]] = None) -> str:
    """Tạo Atom XML entry để đăng bài lên Blogger."""
    cats = ""
    if labels:
        cats = "\n".join(
            f'  <category scheme="http://www.blogger.com/atom/ns#" term="{lbl}"/>'
            for lbl in labels
        )

    # Escape nội dung HTML trong CDATA để tránh lỗi XML
    safe_content = content.replace("]]>", "]]]]><![CDATA[>")

    return f"""<?xml version='1.0' encoding='UTF-8'?>
<entry xmlns='{ATOM_NS}'>
  <title type='text'>{_xml_escape(title)}</title>
  <content type='html'><![CDATA[{safe_content}]]></content>
{cats}
  <app:control xmlns:app='{APP_NS}'>
    <app:draft>no</app:draft>
  </app:control>
</entry>"""


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
    )


def _parse_post_response(xml_text: str) -> dict:
    """Parse Atom XML response từ Blogger để lấy post URL và ID."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"url": "", "id": ""}

    ns = {"a": ATOM_NS}

    url = ""
    for link in root.findall("a:link", ns):
        if link.get("rel") == "alternate":
            url = link.get("href", "")
            break

    post_id = ""
    id_el = root.find("a:id", ns)
    if id_el is not None and id_el.text:
        parts = id_el.text.split("post-")
        if len(parts) > 1:
            post_id = parts[-1]

    return {"url": url, "id": post_id}


# ─── OAuth Flow ───────────────────────────────────────────────────────────────

def build_oauth_url(db: Session, redirect_uri: str, user_id=None) -> str:
    client_id = get_setting(db, "google_client_id", user_id=user_id)
    if not client_id:
        raise ValueError("Google Client ID chưa được cấu hình trong Cài đặt")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(BLOGGER_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(db: Session, code: str, redirect_uri: str, user_id=None) -> dict:
    client_id = get_setting(db, "google_client_id", user_id=user_id)
    client_secret = get_setting(db, "google_client_secret", user_id=user_id)

    with httpx.Client() as client:
        resp = client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        return resp.json()


def get_user_info(access_token: str) -> dict:
    with httpx.Client() as client:
        resp = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def refresh_access_token(db: Session, account: GoogleAccount) -> str:
    """Refresh token nếu hết hạn, trả về access token hợp lệ."""
    now = datetime.utcnow()
    if account.token_expiry and account.token_expiry > now + timedelta(minutes=5):
        return account.access_token

    client_id = get_setting(db, "google_client_id")
    client_secret = get_setting(db, "google_client_secret")

    with httpx.Client() as client:
        resp = client.post(GOOGLE_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": account.refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        data = resp.json()

    account.access_token = data["access_token"]
    account.token_expiry = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 3600))
    db.commit()
    return account.access_token


# ─── Blogger GData API ────────────────────────────────────────────────────────

def get_user_blogs(db: Session, account: GoogleAccount) -> list[dict]:
    """Lấy danh sách blog. Thử Blogger API v3 trước, fallback sang GData XML."""
    token = refresh_access_token(db, account)

    # Thử Blogger API v3 — vẫn hoạt động kể cả khi bị xóa khỏi API Library
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(
                f"{BLOGGER_API_V3_BASE}/users/self/blogs",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {
                        "id": item.get("id", ""),
                        "name": item.get("name", "Unnamed Blog"),
                        "url": item.get("url", ""),
                    }
                    for item in data.get("items", [])
                ]
    except Exception:
        pass

    # Fallback: GData Atom XML (không dùng alt=json)
    with httpx.Client(timeout=20) as client:
        resp = client.get(
            f"{BLOGGER_FEEDS_BASE}/default/blogs",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()

    ns = {"a": ATOM_NS}
    root = ET.fromstring(resp.text)
    blogs = []
    for entry in root.findall("a:entry", ns):
        id_el = entry.find("a:id", ns)
        raw_id = id_el.text if id_el is not None else ""
        blog_id = raw_id.split("blog-")[-1] if "blog-" in raw_id else raw_id

        title_el = entry.find("a:title", ns)
        blog_name = title_el.text if title_el is not None else "Unnamed Blog"

        blog_url = ""
        for link in entry.findall("a:link", ns):
            if link.get("rel") == "alternate":
                blog_url = link.get("href", "")
                break

        blogs.append({"id": blog_id, "name": blog_name, "url": blog_url})

    return blogs


def get_blog_by_url(access_token: str, blog_url: str) -> dict:
    """Lấy thông tin blog theo URL để thêm thủ công."""
    if not blog_url.startswith("http"):
        blog_url = "https://" + blog_url
    with httpx.Client(timeout=20) as client:
        resp = client.get(
            f"{BLOGGER_API_V3_BASE}/blogs/byurl",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"url": blog_url},
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "id": data.get("id", ""),
        "name": data.get("name", "Unnamed Blog"),
        "url": data.get("url", blog_url),
    }


def publish_post(
    db: Session,
    account: GoogleAccount,
    blog_id: str,
    title: str,
    content: str,
    labels: Optional[list[str]] = None,
) -> dict:
    """Đăng bài mới lên Blogspot qua Blogger API v3. Trả về {"url": ..., "id": ...}."""
    token = refresh_access_token(db, account)
    body: dict = {"kind": "blogger#post", "title": title, "content": content}
    if labels:
        body["labels"] = labels

    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{BLOGGER_API_V3_BASE}/blogs/{blog_id}/posts/",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    return {"url": data.get("url", ""), "id": data.get("id", "")}


def update_post(
    db: Session,
    account: GoogleAccount,
    blog_id: str,
    post_id: str,
    title: str,
    content: str,
) -> dict:
    """Cập nhật nội dung bài viết đã đăng qua Blogger API v3."""
    token = refresh_access_token(db, account)
    body = {"kind": "blogger#post", "id": post_id, "title": title, "content": content}

    with httpx.Client(timeout=60) as client:
        resp = client.put(
            f"{BLOGGER_API_V3_BASE}/blogs/{blog_id}/posts/{post_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    return {"url": data.get("url", ""), "id": data.get("id", post_id)}
