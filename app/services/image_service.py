import logging
import random
import re
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


def build_hero_image_url(prompt: str, width: int = 1200, height: int = 630) -> str:
    """Pollinations.ai — AI sinh ảnh đại diện, không cần API key."""
    seed = random.randint(1, 99999)
    encoded = quote(prompt[:500])  # giới hạn độ dài prompt
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&nologo=true&seed={seed}"
    )


def fetch_pixabay_image(query: str, api_key: str) -> str | None:
    """Pixabay API — ảnh thật, trả về URL hoặc None nếu thất bại."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://pixabay.com/api/",
                params={
                    "key": api_key,
                    "q": query,
                    "image_type": "photo",
                    "orientation": "horizontal",
                    "safesearch": "true",
                    "per_page": 5,
                    "min_width": 600,
                },
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            if hits:
                return random.choice(hits[:3]).get("webformatURL")
    except Exception as e:
        logger.warning(f"Pixabay fetch failed for '{query}': {e}")
    return None


def _pixabay_figure_html(query: str, api_key: str) -> str:
    """Trả về HTML <figure> ảnh Pixabay, hoặc chuỗi rỗng nếu không tìm được."""
    url = fetch_pixabay_image(query, api_key)
    if not url:
        return ""
    safe_query = query.replace('"', "")
    return (
        f'\n<figure style="margin:1.5rem 0;text-align:center;">'
        f'<img src="{url}" alt="{safe_query}" '
        f'style="max-width:100%;border-radius:8px;" loading="lazy">'
        f"</figure>\n"
    )


def insert_images_into_content(
    content: str,
    title: str,
    image_prompt: str,
    image_queries: list[str],
    pixabay_api_key: str,
) -> str:
    """
    Chèn 2-4 ảnh vào bài viết, số lượng ngẫu nhiên theo độ dài nội dung:
    - Đầu bài: 1 ảnh Pollinations.ai (hero image)
    - Trong bài: 1-3 ảnh Pixabay phân bổ đều vào các section h2
    """
    # ── Hero image (Pollinations) ─────────────────────────────────────────────
    prompt = image_prompt.strip() if image_prompt else title
    hero_url = build_hero_image_url(prompt)
    hero_html = (
        f'<figure style="margin:0 0 2rem;text-align:center;">'
        f'<img src="{hero_url}" alt="{title}" '
        f'style="max-width:100%;width:100%;border-radius:8px;" loading="lazy">'
        f"</figure>\n"
    )

    # ── Tách content thành các phần theo thẻ <h2 ─────────────────────────────
    sections = re.split(r"(?=<h2[\s>])", content)
    sections[0] = hero_html + sections[0]

    # ── Xác định số ảnh Pixabay cần chèn dựa theo độ dài bài ─────────────────
    if pixabay_api_key and image_queries:
        word_count = len(re.sub(r"<[^>]+>", " ", content).split())
        if word_count < 400:
            extra_count = 1
        elif word_count < 800:
            extra_count = random.randint(1, 2)
        else:
            extra_count = random.randint(2, 3)

        num_sections = len(sections) - 1  # số section h2 (bỏ intro)
        if num_sections > 0:
            # Chọn section indices phân bổ đều
            if extra_count >= num_sections:
                chosen = list(range(1, len(sections)))
            else:
                step = num_sections / extra_count
                chosen = [int(round(1 + i * step)) for i in range(extra_count)]
                chosen = [min(c, len(sections) - 1) for c in chosen]

            for i, section_idx in enumerate(chosen):
                if i >= len(image_queries):
                    break
                figure = _pixabay_figure_html(image_queries[i], pixabay_api_key)
                if figure:
                    sections[section_idx] = re.sub(
                        r"(</h2>)",
                        r"\1" + figure,
                        sections[section_idx],
                        count=1,
                    )

    return "".join(sections)
