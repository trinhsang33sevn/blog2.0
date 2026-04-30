"""
Direct API integrations for paid/free AI providers.
All call_* functions return plain text.
"""
import time
import httpx
import logging
from threading import Lock

logger = logging.getLogger(__name__)

OPENAI_BASE = "https://api.openai.com/v1"
GROQ_BASE   = "https://api.groq.com/openai/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

# ─── Model Lists ──────────────────────────────────────────────────────────────

GEMINI_MODELS = [
    {"id": "gemini:gemini-2.0-flash",        "name": "Gemini 2.0 Flash (Nhanh · 1500/ngày) — Google"},
    {"id": "gemini:gemini-2.5-flash-preview-04-17", "name": "Gemini 2.5 Flash Preview (Mạnh nhất free) — Google"},
    {"id": "gemini:gemini-1.5-flash",        "name": "Gemini 1.5 Flash (Ổn định) — Google"},
    {"id": "gemini:gemini-2.5-pro-preview-05-06", "name": "Gemini 2.5 Pro Preview (25/ngày) — Google"},
    {"id": "gemini:gemini-1.5-pro",          "name": "Gemini 1.5 Pro (50/ngày) — Google"},
]

CLAUDE_MODELS = [
    {"id": "claude:claude-opus-4-7",              "name": "Claude Opus 4.7 (Mạnh nhất) — Anthropic"},
    {"id": "claude:claude-sonnet-4-6",            "name": "Claude Sonnet 4.6 (Cân bằng) — Anthropic"},
    {"id": "claude:claude-haiku-4-5-20251001",    "name": "Claude Haiku 4.5 (Nhanh/Rẻ) — Anthropic"},
    {"id": "claude:claude-3-5-sonnet-20241022",   "name": "Claude 3.5 Sonnet — Anthropic"},
]

OPENAI_MODELS = [
    {"id": "openai:gpt-4o",        "name": "GPT-4o (Tốt nhất) — OpenAI"},
    {"id": "openai:gpt-4o-mini",   "name": "GPT-4o Mini (Nhanh/Rẻ) — OpenAI"},
    {"id": "openai:gpt-4-turbo",   "name": "GPT-4 Turbo — OpenAI"},
    {"id": "openai:gpt-3.5-turbo", "name": "GPT-3.5 Turbo (Rẻ nhất) — OpenAI"},
]

GROQ_MODELS = [
    {"id": "groq:llama-3.3-70b-versatile", "name": "Llama 3.3 70B (Tốt nhất) — Groq"},
    {"id": "groq:llama-3.1-8b-instant",    "name": "Llama 3.1 8B (Nhanh nhất) — Groq"},
    {"id": "groq:mixtral-8x7b-32768",      "name": "Mixtral 8x7B — Groq"},
    {"id": "groq:gemma2-9b-it",            "name": "Gemma 2 9B — Groq"},
]

ALL_PAID_MODELS = GEMINI_MODELS + CLAUDE_MODELS + OPENAI_MODELS + GROQ_MODELS

# ─── Gemini multi-key rotation ────────────────────────────────────────────────

_gemini_lock = Lock()
_gemini_key_failed_at: dict[str, float] = {}  # key → epoch time rate-limited
_GEMINI_COOLDOWN_SECS = 65  # wait 65s after 429 before retrying same key


def _gemini_key_available(key: str) -> bool:
    with _gemini_lock:
        failed = _gemini_key_failed_at.get(key)
        return failed is None or (time.time() - failed) > _GEMINI_COOLDOWN_SECS


def _gemini_mark_limited(key: str) -> None:
    with _gemini_lock:
        _gemini_key_failed_at[key] = time.time()
        logger.warning(f"[gemini] Key ...{key[-8:]} rate limited, cooldown {_GEMINI_COOLDOWN_SECS}s")


def _call_gemini_single(api_key: str, model: str, messages: list, max_tokens: int) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    with httpx.Client(timeout=120) as c:
        resp = c.post(f"{GEMINI_BASE}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if not content:
        raise ValueError("Gemini returned empty response")
    return content


def call_gemini(keys_str: str, model: str, messages: list, max_tokens: int = 4000) -> str:
    """
    Gọi Gemini API với nhiều API keys, tự xoay vòng khi bị rate limit.
    keys_str: nhiều keys cách nhau bởi newline.
    """
    keys = [k.strip() for k in keys_str.splitlines() if k.strip()]
    if not keys:
        raise ValueError("Không có Gemini API key nào được cấu hình")

    # Phase 1: thử keys không bị cooldown trước
    for key in keys:
        if not _gemini_key_available(key):
            continue
        try:
            result = _call_gemini_single(key, model, messages, max_tokens)
            with _gemini_lock:
                _gemini_key_failed_at.pop(key, None)
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                _gemini_mark_limited(key)
                continue
            raise

    # Phase 2: nếu tất cả bị cooldown, thử lại theo thứ tự
    logger.warning(f"[gemini] Tất cả {len(keys)} keys bị rate limit, thử lại lần cuối")
    for key in keys:
        try:
            return _call_gemini_single(key, model, messages, max_tokens)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                continue
            raise

    raise RuntimeError(f"Tất cả {len(keys)} Gemini API keys đều bị rate limit. Fallback về OpenRouter.")


# ─── Claude ───────────────────────────────────────────────────────────────────

def call_claude(api_key: str, model: str, messages: list, max_tokens: int = 4000) -> str:
    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    system_msg, user_messages = "", []
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        else:
            user_messages.append(m)

    payload: dict = {"model": model, "max_tokens": max_tokens, "messages": user_messages}
    if system_msg:
        payload["system"] = system_msg

    with httpx.Client(timeout=120) as c:
        resp = c.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    if not text:
        raise ValueError("Claude returned empty response")
    return text


# ─── OpenAI ───────────────────────────────────────────────────────────────────

def call_openai(api_key: str, model: str, messages: list, max_tokens: int = 4000) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    with httpx.Client(timeout=120) as c:
        resp = c.post(f"{OPENAI_BASE}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if not content:
        raise ValueError("OpenAI returned empty response")
    return content


# ─── Groq ─────────────────────────────────────────────────────────────────────

def call_groq(api_key: str, model: str, messages: list, max_tokens: int = 4000) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    with httpx.Client(timeout=60) as c:
        resp = c.post(f"{GROQ_BASE}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if not content:
        raise ValueError("Groq returned empty response")
    return content


# ─── Test connection ──────────────────────────────────────────────────────────

def test_connection(provider: str, api_key: str) -> dict:
    """Test a single API key. Returns {"ok": bool, "message": str}."""
    test_msg = [{"role": "user", "content": "Reply with exactly: OK"}]
    try:
        if provider == "gemini":
            model = "gemini-2.0-flash"
            result = _call_gemini_single(api_key, model, test_msg, max_tokens=10)
        elif provider == "claude":
            model = "claude-haiku-4-5-20251001"
            result = call_claude(api_key, model, test_msg, max_tokens=10)
        elif provider == "openai":
            model = "gpt-4o-mini"
            result = call_openai(api_key, model, test_msg, max_tokens=10)
        elif provider == "groq":
            model = "llama-3.1-8b-instant"
            result = call_groq(api_key, model, test_msg, max_tokens=10)
        else:
            return {"ok": False, "message": f"Provider không hợp lệ: {provider}"}
        return {"ok": True, "message": f"Kết nối thành công! ({model}) → {result[:40]}"}
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 401:
            return {"ok": False, "message": "API key không hợp lệ (401 Unauthorized)"}
        if status == 429:
            return {"ok": False, "message": "Key hợp lệ nhưng đang bị rate limit (429)."}
        try:
            msg = e.response.json().get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return {"ok": False, "message": f"Lỗi {status}: {msg[:120]}"}
    except Exception as e:
        return {"ok": False, "message": f"Lỗi: {str(e)[:120]}"}
