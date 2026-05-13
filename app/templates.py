import jinja2
from fastapi.templating import Jinja2Templates
from .i18n import _, LANGUAGE_LABELS, get_lang
from .config import get_settings

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader("templates"),
    autoescape=True,
    cache_size=0,
)

_PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "claude": "Anthropic Claude",
    "openai": "OpenAI",
    "groq":   "Groq",
}


def _model_display(model_id: str) -> str:
    """Strip provider prefix và trả về tên model dễ đọc.
    'claude:claude-opus-4-7' → 'claude-opus-4-7 (Claude)'
    'meta-llama/llama-3.3-70b-instruct:free' → 'meta-llama/llama-3.3-70b-instruct:free'
    """
    if not model_id:
        return model_id or ""
    for prefix, label in _PROVIDER_LABELS.items():
        if model_id.startswith(f"{prefix}:"):
            inner = model_id[len(prefix) + 1:]
            return f"{inner} ({label})"
    return model_id


_settings = get_settings()

_env.filters["model_display"] = _model_display
_env.globals["_"] = _
_env.globals["LANGUAGE_LABELS"] = LANGUAGE_LABELS
_env.globals["get_lang"] = get_lang
_env.globals["GA_ID"] = _settings.GOOGLE_ANALYTICS_ID
_env.globals["GSC_VERIFY"] = _settings.GOOGLE_SITE_VERIFICATION

templates = Jinja2Templates(env=_env)
