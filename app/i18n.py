"""Lightweight i18n — JSON-based translation with per-request ContextVar."""
import json
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

TRANSLATIONS_DIR = Path(__file__).parent.parent / "translations"
SUPPORTED_LANGUAGES = {"vi", "en", "fr", "it"}
DEFAULT_LANG = "vi"

_current_lang: ContextVar[str] = ContextVar("current_lang", default=DEFAULT_LANG)

LANGUAGE_LABELS = {
    "vi": "🇻🇳 Tiếng Việt",
    "en": "🇬🇧 English",
    "fr": "🇫🇷 Français",
    "it": "🇮🇹 Italiano",
}


@lru_cache(maxsize=8)
def _load(lang: str) -> dict:
    path = TRANSLATIONS_DIR / f"{lang}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def get_lang() -> str:
    return _current_lang.get()


def set_lang(lang: str) -> None:
    _current_lang.set(lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANG)


def _(key: str, **kwargs) -> str:
    """Translate key in current language, fallback to Vietnamese, then key itself."""
    lang = _current_lang.get()
    text = _load(lang).get(key)
    if text is None and lang != DEFAULT_LANG:
        text = _load(DEFAULT_LANG).get(key)
    if text is None:
        text = key
    return text.format(**kwargs) if kwargs else text
