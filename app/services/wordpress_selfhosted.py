import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".gif": "image/gif"}


def _auth(username: str, app_password: str) -> str:
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return f"Basic {token}"


def _base(site_url: str) -> str:
    return site_url.rstrip("/")


def upload_media(site_url: str, username: str, app_password: str, file_path: Path) -> str | None:
    """Upload image to self-hosted WordPress media library. Returns hosted URL or None."""
    mime = _MIME.get(file_path.suffix.lower(), "image/jpeg")
    try:
        with httpx.Client(timeout=60) as c:
            resp = c.post(
                f"{_base(site_url)}/wp-json/wp/v2/media",
                auth=(username, app_password),
                headers={
                    "Content-Type": mime,
                    "Content-Disposition": f'attachment; filename="{file_path.name}"',
                },
                content=file_path.read_bytes(),
            )
            resp.raise_for_status()
            return resp.json().get("source_url")
    except Exception as e:
        logger.warning("WP self-hosted media upload failed: %s", e)
    return None


def test_connection(site_url: str, username: str, app_password: str) -> dict:
    url = f"{_base(site_url)}/wp-json/wp/v2/users/me"
    req = urllib.request.Request(url)
    req.add_header("Authorization", _auth(username, app_password))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {"ok": True, "name": data.get("name", username)}
    except urllib.error.HTTPError as e:
        raise ValueError(f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        raise ValueError(str(e))


def _get_or_create_tag_ids(site_url: str, auth: str, tag_names: list) -> list:
    ids = []
    for name in tag_names:
        try:
            search_url = f"{_base(site_url)}/wp-json/wp/v2/tags?search={urllib.parse.quote(name)}&per_page=1"
            req = urllib.request.Request(search_url)
            req.add_header("Authorization", auth)
            with urllib.request.urlopen(req, timeout=10) as resp:
                tags = json.loads(resp.read())
                matched = next((t for t in tags if t["name"].lower() == name.lower()), None)
                if matched:
                    ids.append(matched["id"])
                    continue
            create_url = f"{_base(site_url)}/wp-json/wp/v2/tags"
            data = json.dumps({"name": name}).encode()
            req2 = urllib.request.Request(create_url, data=data, method="POST")
            req2.add_header("Authorization", auth)
            req2.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                tag = json.loads(resp2.read())
                ids.append(tag["id"])
        except Exception:
            pass
    return ids


def publish_post(site_url: str, username: str, app_password: str,
                 title: str, content: str, tags: list | None = None) -> dict:
    auth = _auth(username, app_password)
    payload: dict = {"title": title, "content": content, "status": "publish"}
    if tags:
        tag_ids = _get_or_create_tag_ids(site_url, auth, tags)
        if tag_ids:
            payload["tags"] = tag_ids

    url = f"{_base(site_url)}/wp-json/wp/v2/posts"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return {"id": str(result.get("id", "")), "url": result.get("link", "")}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise ValueError(f"HTTP {e.code}: {body[:300]}")


def update_post(site_url: str, username: str, app_password: str,
                post_id: str, title: str, content: str) -> dict:
    auth = _auth(username, app_password)
    url = f"{_base(site_url)}/wp-json/wp/v2/posts/{post_id}"
    data = json.dumps({"title": title, "content": content}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return {"id": str(result.get("id", "")), "url": result.get("link", "")}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise ValueError(f"HTTP {e.code}: {body[:300]}")
