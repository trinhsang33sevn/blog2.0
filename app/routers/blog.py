from html import escape as _esc
import io

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ..blog_data import ARTICLES, ARTICLES_BY_SLUG, CATEGORIES, CATEGORY_TRANSLATIONS
from ..content_translations import EN as _CT_EN, FR as _CT_FR, IT as _CT_IT
from ..i18n import get_lang, set_lang, SUPPORTED_LANGUAGES
from ..templates import templates

_CONTENT_TRANS: dict[str, dict[str, str]] = {"en": _CT_EN, "fr": _CT_FR, "it": _CT_IT}

router = APIRouter()

# ---------------------------------------------------------------------------
# Category styles: (gradient-start, gradient-end, accent-color)
# ---------------------------------------------------------------------------
_CAT_STYLES: dict[str, tuple[str, str, str]] = {
    "Kiến thức":           ("#1e1b4b", "#312e81", "#818cf8"),
    "So sánh":             ("#0f172a", "#1e3a8a", "#60a5fa"),
    "Kiến thức SEO":       ("#022c22", "#14532d", "#34d399"),
    "Hướng dẫn":           ("#1c1300", "#78350f", "#fbbf24"),
    "Chiến lược SEO":      ("#1e0a3c", "#4c1d95", "#a78bfa"),
    "Kỹ thuật SEO":        ("#061a28", "#075985", "#38bdf8"),
    "Affiliate Marketing": ("#1c1100", "#854d0e", "#fde047"),
}
_DEFAULT_STYLE = ("#0d1117", "#161b22", "#58a6ff")

_PNG_CACHE: dict[str, bytes] = {}
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _wrap(text: str, max_chars: int = 24) -> list[str]:
    """Split text into lines of at most max_chars characters (on word boundary)."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w
        if len(lines) == 3:
            break
    if cur and len(lines) < 3:
        lines.append(cur)
    return lines


def _generate_svg(article: dict) -> str:
    """Return an SVG thumbnail relevant to the article (category-coloured, title shown)."""
    cat = article["category"]
    title = article["title"]
    date = article.get("date", "")

    c1, c2, accent = _CAT_STYLES.get(cat, _DEFAULT_STYLE)
    lines = _wrap(title, 24)
    base_y = 340 - (len(lines) - 1) * 48
    title_svg = "".join(
        f'<text x="40" y="{base_y + i * 48}" '
        f'font-family="\'Segoe UI\',system-ui,sans-serif" font-size="28" '
        f'font-weight="800" fill="#e6edf3">{_esc(line)}</text>'
        for i, line in enumerate(lines)
    )

    # Category badge width (approx 8px per char + padding)
    badge_w = max(len(cat) * 8 + 28, 80)

    # Decorative SVG paths per category
    decorations = {
        "Kiến thức": f'<circle cx="640" cy="80" r="60" fill="none" stroke="{accent}" stroke-opacity="0.3" stroke-width="2"/><circle cx="640" cy="80" r="40" fill="none" stroke="{accent}" stroke-opacity="0.2" stroke-width="1.5"/><line x1="610" y1="80" x2="670" y2="80" stroke="{accent}" stroke-opacity="0.4" stroke-width="1.5"/><line x1="640" y1="50" x2="640" y2="110" stroke="{accent}" stroke-opacity="0.4" stroke-width="1.5"/>',
        "So sánh": f'<rect x="590" y="50" width="50" height="60" rx="4" fill="none" stroke="{accent}" stroke-opacity="0.35" stroke-width="1.5"/><rect x="650" y="70" width="50" height="40" rx="4" fill="none" stroke="{accent}" stroke-opacity="0.25" stroke-width="1.5"/><line x1="590" y1="120" x2="700" y2="120" stroke="{accent}" stroke-opacity="0.3" stroke-width="2"/>',
        "Kiến thức SEO": f'<circle cx="645" cy="75" r="35" fill="none" stroke="{accent}" stroke-opacity="0.35" stroke-width="2"/><line x1="668" y1="98" x2="690" y2="120" stroke="{accent}" stroke-opacity="0.45" stroke-width="3" stroke-linecap="round"/>',
        "Hướng dẫn": f'<rect x="595" y="45" width="110" height="90" rx="6" fill="none" stroke="{accent}" stroke-opacity="0.3" stroke-width="1.5"/><line x1="610" y1="68" x2="692" y2="68" stroke="{accent}" stroke-opacity="0.3" stroke-width="1.5"/><line x1="610" y1="85" x2="692" y2="85" stroke="{accent}" stroke-opacity="0.25" stroke-width="1.5"/><line x1="610" y1="102" x2="660" y2="102" stroke="{accent}" stroke-opacity="0.2" stroke-width="1.5"/>',
        "Chiến lược SEO": f'<polyline points="590,120 620,90 650,100 680,60 710,40" fill="none" stroke="{accent}" stroke-opacity="0.45" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="710" cy="40" r="5" fill="{accent}" fill-opacity="0.6"/>',
        "Kỹ thuật SEO": f'<circle cx="650" cy="80" r="45" fill="none" stroke="{accent}" stroke-opacity="0.25" stroke-width="2"/><circle cx="650" cy="80" r="12" fill="none" stroke="{accent}" stroke-opacity="0.45" stroke-width="2"/><line x1="650" y1="30" x2="650" y2="50" stroke="{accent}" stroke-opacity="0.3" stroke-width="2"/><line x1="650" y1="110" x2="650" y2="130" stroke="{accent}" stroke-opacity="0.3" stroke-width="2"/><line x1="600" y1="80" x2="620" y2="80" stroke="{accent}" stroke-opacity="0.3" stroke-width="2"/><line x1="680" y1="80" x2="700" y2="80" stroke="{accent}" stroke-opacity="0.3" stroke-width="2"/>',
        "Affiliate Marketing": f'<circle cx="650" cy="75" r="40" fill="none" stroke="{accent}" stroke-opacity="0.3" stroke-width="2"/><text x="650" y="85" font-family="system-ui" font-size="32" font-weight="800" fill="{accent}" fill-opacity="0.5" text-anchor="middle">$</text>',
    }
    deco = decorations.get(cat, "")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
    <linearGradient id="ov" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{c1}" stop-opacity="0"/>
      <stop offset="50%" stop-color="{c1}" stop-opacity="0.65"/>
      <stop offset="100%" stop-color="{c1}" stop-opacity="0.96"/>
    </linearGradient>
    <pattern id="dots" x="0" y="0" width="36" height="36" patternUnits="userSpaceOnUse">
      <circle cx="18" cy="18" r="1.2" fill="{accent}" fill-opacity="0.18"/>
    </pattern>
  </defs>
  <rect width="800" height="450" fill="url(#bg)"/>
  <rect width="800" height="450" fill="url(#dots)"/>
  <circle cx="700" cy="100" r="220" fill="{accent}" fill-opacity="0.06"/>
  <circle cx="730" cy="60" r="100" fill="{accent}" fill-opacity="0.09"/>
  <circle cx="80" cy="390" r="130" fill="{accent}" fill-opacity="0.05"/>
  {deco}
  <rect width="800" height="450" fill="url(#ov)"/>
  <rect x="32" y="24" width="{badge_w}" height="26" rx="13" fill="{accent}" fill-opacity="0.2"/>
  <rect x="32" y="24" width="{badge_w}" height="26" rx="13" fill="none" stroke="{accent}" stroke-opacity="0.5" stroke-width="1"/>
  <text x="44" y="41" font-family="'Segoe UI',system-ui,sans-serif" font-size="11" font-weight="700" fill="{accent}" letter-spacing="0.8">{_esc(cat.upper())}</text>
  {title_svg}
  <text x="40" y="420" font-family="system-ui,sans-serif" font-size="13" fill="#6e7681">{_esc(date)}</text>
  <text x="768" y="430" font-family="system-ui,sans-serif" font-size="11" fill="#30363d" text-anchor="end">AutoBlogspot</text>
  <rect x="0" y="0" width="800" height="450" fill="none" stroke="{accent}" stroke-opacity="0.08" stroke-width="1"/>
</svg>"""


def _generate_png(article: dict) -> bytes:
    """Return a 1200×630 PNG OG image for social sharing (Facebook, Zalo, etc.)."""
    slug = article.get("slug", "")
    if slug and slug in _PNG_CACHE:
        return _PNG_CACHE[slug]

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return b""

    cat   = article["category"]
    title = article["title"]
    c1, c2, accent = _CAT_STYLES.get(cat, _DEFAULT_STYLE)
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    ar, ag, ab = _hex_to_rgb(accent)

    W, H = 1200, 630

    # Gradient background
    bg = Image.new("RGB", (W, H))
    bg_draw = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        bg_draw.line([(0, y), (W, y)], fill=(
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t),
        ))

    # Decorative accent circles via transparent overlay
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_d = ImageDraw.Draw(ov)
    ov_d.ellipse([860, -120, 1340, 360], fill=(ar, ag, ab, 18))
    ov_d.ellipse([920, -40,  1200, 240], fill=(ar, ag, ab, 28))
    ov_d.ellipse([-30, 460,  250,  740], fill=(ar, ag, ab, 15))

    img = Image.alpha_composite(bg.convert("RGBA"), ov)
    draw = ImageDraw.Draw(img)

    try:
        f_title = ImageFont.truetype(_FONT_BOLD, 56)
        f_cat   = ImageFont.truetype(_FONT_BOLD, 22)
        f_brand = ImageFont.truetype(_FONT_REG,  20)
    except OSError:
        f_title = f_cat = f_brand = ImageFont.load_default()

    # Category badge
    cat_text = cat.upper()
    cb = draw.textbbox((0, 0), cat_text, font=f_cat)
    bx, by = 80, 96
    draw.rounded_rectangle(
        [bx - 2, by - 10, bx + (cb[2] - cb[0]) + 34, by + (cb[3] - cb[1]) + 10],
        radius=20,
        fill=(ar, ag, ab, 45),
        outline=(ar, ag, ab, 160),
        width=1,
    )
    draw.text((bx + 16, by), cat_text, font=f_cat, fill=(ar, ag, ab, 255))

    # Title text
    lines = _wrap(title, 32)
    ty = 300 - len(lines) * 38
    for line in lines:
        draw.text((80, ty), line, font=f_title, fill=(230, 237, 243, 255))
        ty += 76

    # Brand watermark
    draw.text((80, 583), "autoblogspot.com", font=f_brand, fill=(110, 118, 129, 255))

    # Flatten RGBA → RGB and encode
    final = Image.new("RGB", (W, H), (r1, g1, b1))
    final.paste(img.convert("RGB"), mask=img.split()[3])
    buf = io.BytesIO()
    final.save(buf, format="PNG", optimize=True)
    result = buf.getvalue()
    if slug:
        _PNG_CACHE[slug] = result
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cat_map(lang: str) -> dict[str, str]:
    """Return {vi_category: translated_category} for current language."""
    return {vi: t.get(lang, vi) for vi, t in CATEGORY_TRANSLATIONS.items()}


def _localized(article: dict, lang: str, cmap: dict[str, str]) -> dict:
    """Return article with localized title, description, content, display_category."""
    cat_vi = article["category"]
    slug   = article.get("slug", "")
    result = {**article, "display_category": cmap.get(cat_vi, cat_vi), "content_lang": "vi"}
    if lang != "vi":
        if article.get(f"title_{lang}"):
            result["title"] = article[f"title_{lang}"]
        if article.get(f"desc_{lang}"):
            result["description"] = article[f"desc_{lang}"]
        # Content: check inline field first, then content_translations module
        content_translated = (
            article.get(f"content_{lang}")
            or _CONTENT_TRANS.get(lang, {}).get(slug)
        )
        if content_translated:
            result["content"] = content_translated
            result["content_lang"] = lang
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/blog/image/{slug}")
def blog_image(slug: str):
    """Serve a dynamically generated SVG thumbnail for each article."""
    article = ARTICLES_BY_SLUG.get(slug)
    if not article:
        # Fallback: generic SVG
        article = {"category": "Blog", "title": slug.replace("-", " ").title(), "date": ""}
    svg = _generate_svg(article)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/blog/og/{slug}")
def blog_og_png(slug: str):
    """Serve a PNG OG image (1200×630) suitable for Facebook, Zalo, Twitter previews."""
    article = ARTICLES_BY_SLUG.get(slug)
    if not article:
        article = {"slug": slug, "category": "Blog", "title": slug.replace("-", " ").title(), "date": ""}
    png = _generate_png(article)
    if not png:
        return Response(status_code=404)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-Robots-Tag": "noindex"},
    )


_PER_PAGE = 30

@router.get("/blog", response_class=HTMLResponse)
def blog_list(request: Request, category: str = "", page: int = 1):
    lang = get_lang()
    cmap = _cat_map(lang)

    # Filter by category, then sort newest first
    filtered = ARTICLES if not category else [a for a in ARTICLES if a["category"] == category]
    sorted_articles = sorted(filtered, key=lambda a: a.get("date", ""), reverse=True)

    total = len(sorted_articles)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * _PER_PAGE
    page_articles = sorted_articles[offset:offset + _PER_PAGE]

    localized = [_localized(a, lang, cmap) for a in page_articles]
    cat_display = {vi: cmap.get(vi, vi) for vi in CATEGORIES}
    return templates.TemplateResponse(request, "blog_list.html", {
        "articles":       localized,
        "categories":     CATEGORIES,
        "cat_display":    cat_display,
        "active_category": category,
        "lang":           lang,
        "page":           page,
        "total_pages":    total_pages,
        "total_articles": total,
    })


@router.get("/blog/{slug}", response_class=HTMLResponse)
def blog_article(request: Request, slug: str):
    from fastapi import HTTPException
    article = ARTICLES_BY_SLUG.get(slug)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    lang = get_lang()
    cmap = _cat_map(lang)
    loc_article = _localized(article, lang, cmap)
    idx = next(i for i, a in enumerate(ARTICLES) if a["slug"] == slug)
    related = [_localized(a, lang, cmap) for a in ARTICLES
               if a["category"] == article["category"] and a["slug"] != slug][:3]
    prev_article = _localized(ARTICLES[idx - 1], lang, cmap) if idx > 0 else None
    next_article = _localized(ARTICLES[idx + 1], lang, cmap) if idx < len(ARTICLES) - 1 else None
    return templates.TemplateResponse(request, "blog_article.html", {
        "article": loc_article,
        "related": related,
        "prev_article": prev_article,
        "next_article": next_article,
        "lang": lang,
    })


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request, lang: str = ""):
    if lang and lang in SUPPORTED_LANGUAGES:
        set_lang(lang)
    return templates.TemplateResponse(request, "terms.html", {})


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {})


@router.get("/faq", response_class=HTMLResponse)
def faq_page(request: Request):
    return templates.TemplateResponse(request, "faq.html", {})


@router.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    return templates.TemplateResponse(request, "compare.html", {})
