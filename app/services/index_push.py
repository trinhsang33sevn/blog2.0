"""
Index acceleration: called immediately after each article is published.

Methods (all free):
1. Google Indexing API  — URL_UPDATED push, requires service account JSON
2. IndexNow             — instant Bing/Yandex notification
3. Sitemap ping         — HTTP ping to Google + Bing sitemap endpoints
4. Blog ping (RPC)      — XML-RPC ping to Ping-O-Matic + others
"""
import json
import logging
import time
import base64
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
_GOOGLE_INDEXING_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
_INDEXNOW_URL        = "https://api.indexnow.org/indexnow"

# ── Google Indexing API ────────────────────────────────────────────────────────

def _google_access_token(sa_json: str) -> str | None:
    """Exchange service account JSON for a short-lived access token (RS256 JWT)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding as _padding

        sa = json.loads(sa_json)
        pem = sa["private_key"].encode()
        email = sa["client_email"]

        now = int(time.time())
        hdr = base64.urlsafe_b64encode(
            json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        pld = base64.urlsafe_b64encode(json.dumps({
            "iss": email,
            "scope": "https://www.googleapis.com/auth/indexing",
            "aud": _GOOGLE_TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }).encode()).rstrip(b"=").decode()

        key = serialization.load_pem_private_key(pem, password=None)
        sig = key.sign(f"{hdr}.{pld}".encode(), _padding.PKCS1v15(), hashes.SHA256())
        jwt = f"{hdr}.{pld}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"

        with httpx.Client(timeout=15) as c:
            r = c.post(_GOOGLE_TOKEN_URL, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt,
            })
            r.raise_for_status()
            return r.json().get("access_token")
    except ImportError:
        logger.warning("index_push: cryptography not installed — Google Indexing API skipped")
    except Exception as e:
        logger.warning("index_push: Google token error: %s", e)
    return None


def push_google_indexing_api(url: str, sa_json: str) -> bool:
    """Notify Google Indexing API about a new/updated URL. Returns True on success."""
    token = _google_access_token(sa_json)
    if not token:
        return False
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                _GOOGLE_INDEXING_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"url": url, "type": "URL_UPDATED"},
            )
        ok = r.status_code == 200
        logger.info("Google Indexing API [%d] %s", r.status_code, url)
        return ok
    except Exception as e:
        logger.warning("index_push: Google Indexing API error: %s", e)
    return False


# ── IndexNow ──────────────────────────────────────────────────────────────────

def push_indexnow(url: str, key: str) -> bool:
    """Submit URL to IndexNow (Bing, Yandex, etc.). Returns True on success.
    Key verification file must exist at https://{host}/{key}.txt — only works
    for WordPress self-hosted where we control the domain.
    """
    parsed = urlparse(url)
    host = parsed.netloc
    if not host:
        return False
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(_INDEXNOW_URL, json={
                "host": host,
                "key": key,
                "keyLocation": f"https://{host}/{key}.txt",
                "urlList": [url],
            })
        ok = r.status_code in (200, 202)
        logger.info("IndexNow [%d] %s", r.status_code, url)
        return ok
    except Exception as e:
        logger.warning("index_push: IndexNow error: %s", e)
    return False


# ── Sitemap ping ──────────────────────────────────────────────────────────────

def ping_sitemaps(url: str) -> None:
    """Ping Google and Bing sitemap endpoints for the blog that contains url."""
    parsed = urlparse(url)
    sitemap = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    for engine_ping in [
        f"https://www.google.com/ping?sitemap={sitemap}",
        f"https://www.bing.com/ping?sitemap={sitemap}",
    ]:
        try:
            with httpx.Client(timeout=10) as c:
                r = c.get(engine_ping)
            logger.info("Sitemap ping [%d] %s", r.status_code, engine_ping[:70])
        except Exception as e:
            logger.warning("index_push: sitemap ping error: %s", e)


# ── Blog RPC ping ─────────────────────────────────────────────────────────────

_RPC_SERVICES = [
    "https://rpc.pingomatic.com/",
    "https://ping.blogs.yam.com/",
    "https://blogsearch.google.com/ping/RPC2",
]

_RPC_BODY = """\
<?xml version="1.0"?>
<methodCall>
  <methodName>weblogUpdates.extendedPing</methodName>
  <params>
    <param><value><string>{name}</string></value></param>
    <param><value><string>{url}</string></value></param>
    <param><value><string>{post_url}</string></value></param>
  </params>
</methodCall>"""


def ping_rpc_services(post_url: str, blog_name: str = "") -> None:
    """Ping standard XML-RPC blog ping services."""
    parsed = urlparse(post_url)
    blog_url = f"{parsed.scheme}://{parsed.netloc}"
    name = blog_name or parsed.netloc
    body = _RPC_BODY.format(name=name, url=blog_url, post_url=post_url).encode()
    for svc in _RPC_SERVICES:
        try:
            with httpx.Client(timeout=8) as c:
                r = c.post(svc, content=body,
                           headers={"Content-Type": "text/xml; charset=utf-8"})
            logger.info("RPC ping [%d] %s", r.status_code, svc)
        except Exception as e:
            logger.warning("index_push: RPC ping %s error: %s", svc, e)


# ── Combined entry point ───────────────────────────────────────────────────────

def push_all(url: str, db, user_id: int, platform: str = "",
             blog_name: str = "") -> None:
    """
    Run all configured index push methods immediately after article publish.
    Safe to call from background thread — all errors are caught and logged.
    """
    if not url or not url.startswith("http"):
        return

    from . import openrouter as _or

    # 1. Sitemap ping — always, no config needed
    ping_sitemaps(url)

    # 2. Blog RPC ping — always
    ping_rpc_services(url, blog_name)

    # 3. IndexNow — only for self-hosted WordPress (we control the domain)
    if platform == "wordpress_selfhosted":
        key = _or.get_setting(db, "indexnow_key", user_id=user_id)
        if key:
            push_indexnow(url, key)

    # 4. Google Indexing API — all platforms if service account configured
    sa_json = _or.get_setting(db, "google_indexing_sa", user_id=user_id)
    if sa_json:
        push_google_indexing_api(url, sa_json)
