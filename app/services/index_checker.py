import random
import httpx

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def check_google_index(url: str) -> bool:
    """Check if a URL is indexed by Google using site: search."""
    query = f"site:{url}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                "https://www.google.com/search",
                params={"q": query, "num": "5", "hl": "en"},
                headers=headers,
            )

        if resp.status_code == 429:
            return False

        page_text = resp.text.lower()

        no_results_indicators = [
            "did not match any documents",
            "no results found",
            "không khớp với bất kỳ tài liệu nào",
        ]
        for indicator in no_results_indicators:
            if indicator in page_text:
                return False

        # Check for result stats element
        if 'id="result-stats"' in page_text:
            idx = page_text.find('id="result-stats"')
            snippet = page_text[idx:idx + 200]
            if '"0 ' not in snippet and "0 kết" not in snippet:
                return True

        # Check for data-ved links (actual search results)
        if 'data-ved="' in page_text and url.split("/")[2] in page_text:
            return True

        return False

    except Exception:
        return False
