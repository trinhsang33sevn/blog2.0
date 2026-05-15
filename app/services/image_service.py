import logging
import random
import re
import uuid
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_NEGATIVE_PROMPT = (
    "deformed, bad anatomy, disfigured, poorly drawn face, mutation, mutated, "
    "extra limbs, extra arms, extra legs, extra hands, extra fingers, "
    "missing limb, missing arm, missing leg, missing hand, missing fingers, "
    "floating limbs, disconnected limbs, malformed hands, malformed limbs, "
    "poorly drawn hands, poorly drawn feet, poorly drawn face, "
    "extra heads, two heads, cloned face, double face, duplicate, "
    "gross proportions, distorted face, bad proportions, "
    "long neck, long body, twisted body, deformed body, "
    "blurry, out of focus, low quality, worst quality, low resolution, "
    "watermark, text, logo, signature, username, nsfw, explicit"
)

_QUALITY_SUFFIX = (
    "photorealistic, professional photography, sharp focus, "
    "high resolution, 8k uhd, high quality, detailed, masterpiece"
)


def _build_pollinations_url(prompt: str, width: int, height: int) -> str:
    seed = random.randint(1, 99999)
    full_prompt = f"{prompt.strip()}, {_QUALITY_SUFFIX}"
    encoded = quote(full_prompt[:600])
    neg_encoded = quote(_NEGATIVE_PROMPT)
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&nologo=true&seed={seed}"
        f"&model=flux&negative={neg_encoded}"
    )


def build_hero_image_url(prompt: str, width: int = 1200, height: int = 630) -> str:
    """Returns Pollinations.ai URL directly — no local storage."""
    return _build_pollinations_url(prompt, width, height)


def fetch_pixabay_image(query: str, api_key: str) -> str | None:
    """Returns Pixabay image URL directly — no local storage."""
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
        logger.warning("Pixabay fetch failed for '%s': %s", query, e)
    return None


def _pixabay_figure_html(query: str, api_key: str) -> str:
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


def _pollinations_figure_html(query: str, width: int = 800, height: int = 450) -> str:
    base_prompt = f"professional high quality photo illustration of {query}, realistic, detailed"
    url = _build_pollinations_url(base_prompt, width, height)
    safe_query = query.replace('"', "")
    return (
        f'\n<figure style="margin:1.5rem 0;text-align:center;">'
        f'<img src="{url}" alt="{safe_query}" '
        f'style="max-width:100%;border-radius:8px;" loading="lazy">'
        f"</figure>\n"
    )


def _fetch_image_bytes(url: str) -> tuple[bytes, str] | None:
    """Download image into memory. Returns (bytes, mime_type) or None."""
    try:
        with httpx.Client(timeout=45, follow_redirects=True) as c:
            resp = c.get(url)
            resp.raise_for_status()
            if not resp.content:
                return None
            ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            return resp.content, ct
    except Exception as e:
        logger.warning("Image fetch failed %s: %s", url[:80], e)
    return None


def rehost_images_for_platform(content: str, platform: str, **auth) -> str:
    """
    For WordPress: download each external image into RAM and upload directly
    to WordPress media library — nothing is saved on VPS disk.
    For all other platforms: return content unchanged.

    auth kwargs:
      wordpress          → access_token, site_id
      wordpress_selfhosted → site_url, username, app_password
    """
    if platform not in ("wordpress", "wordpress_selfhosted"):
        return content

    for url in re.findall(r'<img[^>]+src="([^"]+)"', content):
        if not url.startswith("http"):
            continue
        result = _fetch_image_bytes(url)
        if not result:
            continue
        img_bytes, mime = result
        ext = {"image/jpeg": "jpg", "image/png": "png",
               "image/webp": "webp", "image/gif": "gif"}.get(mime, "jpg")
        filename = f"{uuid.uuid4().hex}.{ext}"
        try:
            if platform == "wordpress":
                from . import wordpress as _wp
                new_url = _wp.upload_media_bytes(
                    auth["access_token"], auth["site_id"], img_bytes, mime, filename
                )
            else:
                from . import wordpress_selfhosted as _wp_sh
                new_url = _wp_sh.upload_media_bytes(
                    auth["site_url"], auth["username"], auth["app_password"],
                    img_bytes, mime, filename,
                )
            if new_url:
                content = content.replace(url, new_url)
                logger.info("Image hosted on WP: %s", new_url)
        except Exception as e:
            logger.warning("Image rehost failed for %s: %s", url, e)

    return content


def insert_images_into_content(
    content: str,
    title: str,
    image_prompt: str,
    image_queries: list[str],
    pixabay_api_key: str,
) -> str:
    """
    Embed 2-4 images into article using external URLs (Pollinations / Pixabay).
    No files are saved to disk. For WordPress, rehost_images_for_platform()
    is called separately at publish time.
    """
    prompt = image_prompt.strip() if image_prompt else title
    hero_url = build_hero_image_url(prompt)
    hero_html = (
        f'<figure style="margin:0 0 2rem;text-align:center;">'
        f'<img src="{hero_url}" alt="{title}" '
        f'style="max-width:100%;width:100%;border-radius:8px;" loading="lazy">'
        f"</figure>\n"
    )

    sections = re.split(r"(?=<h2[\s>])", content)
    sections[0] = hero_html + sections[0]

    if image_queries:
        word_count = len(re.sub(r"<[^>]+>", " ", content).split())
        if word_count < 400:
            extra_count = 1
        elif word_count < 800:
            extra_count = random.randint(1, 2)
        else:
            extra_count = random.randint(2, 3)

        num_sections = len(sections) - 1
        if num_sections > 0:
            if extra_count >= num_sections:
                chosen = list(range(1, len(sections)))
            else:
                step = num_sections / extra_count
                chosen = [int(round(1 + i * step)) for i in range(extra_count)]
                chosen = list(dict.fromkeys(min(c, len(sections) - 1) for c in chosen))

            for i, section_idx in enumerate(chosen):
                if i >= len(image_queries):
                    break
                if pixabay_api_key:
                    figure = _pixabay_figure_html(image_queries[i], pixabay_api_key)
                else:
                    figure = _pollinations_figure_html(image_queries[i])
                if figure:
                    sections[section_idx] = re.sub(
                        r"(</h2>)",
                        r"\1" + figure,
                        sections[section_idx],
                        count=1,
                    )

    return "".join(sections)
