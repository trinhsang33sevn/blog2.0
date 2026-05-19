import logging
import random
import httpx

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_BLOCKED_INDICATORS = [
    "captcha", "unusual traffic", "before you continue",
    "g-recaptcha", "please verify", "verify you're human",
]
_NO_RESULT_INDICATORS = [
    "did not match any documents",
    "no results found",
    "không khớp với bất kỳ tài liệu nào",
]


def _parse_indexed(resp: httpx.Response, url: str) -> bool:
    page = resp.text.lower()
    for ind in _NO_RESULT_INDICATORS:
        if ind in page:
            return False
    if 'id="result-stats"' in page:
        idx = page.find('id="result-stats"')
        snippet = page[idx:idx + 200]
        if '"0 ' not in snippet and "0 kết" not in snippet:
            return True
    if 'data-ved="' in page and url.split("/")[2] in page:
        return True
    return False


def _is_blocked(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    page = resp.text.lower()
    return any(ind in page for ind in _BLOCKED_INDICATORS)


def _do_request(url: str, proxy_url: str = None) -> httpx.Response:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    with httpx.Client(timeout=20, follow_redirects=True, proxy=proxy_url or None) as client:
        return client.get(
            "https://www.google.com/search",
            params={"q": f"site:{url}", "num": "5", "hl": "en"},
            headers=headers,
        )


def check_google_index(url: str, proxy_url: str = None) -> bool:
    """Check if a URL is indexed by Google.

    Strategy:
    1. Try direct request (free).
    2. If blocked (429 / CAPTCHA) and proxy_url is set → retry via DataImpulse proxy.
    """
    try:
        resp = _do_request(url)
        if not _is_blocked(resp):
            return _parse_indexed(resp, url)
        logger.warning("Direct Google check blocked for %s — %s", url, resp.status_code)
    except Exception as exc:
        logger.warning("Direct Google check error for %s: %s", url, exc)

    # Fallback: DataImpulse proxy
    if not proxy_url:
        return False

    try:
        logger.info("Retrying via DataImpulse proxy: %s", url)
        resp = _do_request(url, proxy_url=proxy_url)
        return _parse_indexed(resp, url)
    except Exception as exc:
        logger.error("DataImpulse proxy check error for %s: %s", url, exc)
        return False
