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

# key → (cooldown_until: float, fail_count: int)
_gemini_state: dict[str, tuple[float, int]] = {}

_GEMINI_BASE_COOLDOWN  = 65    # giây đầu tiên sau 429
_GEMINI_MAX_COOLDOWN   = 3600  # tối đa 1 giờ


def _gemini_key_available(key: str) -> bool:
    with _gemini_lock:
        state = _gemini_state.get(key)
        if state is None:
            return True
        cooldown_until, _ = state
        return time.time() >= cooldown_until


def _gemini_mark_limited(key: str, retry_after: int | None = None) -> None:
    with _gemini_lock:
        _, fail_count = _gemini_state.get(key, (0.0, 0))
        fail_count += 1
        if retry_after and retry_after > 0:
            cooldown = min(retry_after + 2, _GEMINI_MAX_COOLDOWN)
        else:
            # Adaptive: 65s → 130s → 300s → 600s → max 3600s
            cooldown = min(_GEMINI_BASE_COOLDOWN * (2 ** (fail_count - 1)), _GEMINI_MAX_COOLDOWN)
        cooldown_until = time.time() + cooldown
        _gemini_state[key] = (cooldown_until, fail_count)
        logger.warning(
            f"[gemini] Key ...{key[-8:]} 429 (lần {fail_count}), "
            f"cooldown {cooldown:.0f}s (đến {time.strftime('%H:%M:%S', time.localtime(cooldown_until))})"
        )


def _gemini_mark_ok(key: str) -> None:
    with _gemini_lock:
        _gemini_state.pop(key, None)


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
    Gọi Gemini API với nhiều API keys, xoay vòng khi bị 429.
    - Chỉ thử keys không bị cooldown.
    - Khi 429: parse Retry-After header → adaptive cooldown (65s→130s→300s→max 1h).
    - Khi tất cả keys đều đang cooldown → raise ngay để smart_call fallback về OpenRouter.
    keys_str: nhiều keys cách nhau bởi newline.
    """
    keys = [k.strip() for k in keys_str.splitlines() if k.strip()]
    if not keys:
        raise ValueError("Không có Gemini API key nào được cấu hình")

    # Single lock acquisition to classify all keys at once
    now = time.time()
    with _gemini_lock:
        available = [k for k in keys if _gemini_state.get(k, (now,))[0] <= now]
        if not available:
            in_cooldown = [k for k in keys if k in _gemini_state]
            earliest = min(_gemini_state[k][0] for k in in_cooldown) if in_cooldown else now
    if not available:
        wait = max(0, earliest - time.time())
        raise RuntimeError(
            f"Tất cả {len(keys)} Gemini keys đang bị rate limit. "
            f"Key sớm nhất khả dụng sau ~{wait:.0f}s. Fallback về OpenRouter."
        )

    for key in available:
        try:
            result = _call_gemini_single(key, model, messages, max_tokens)
            _gemini_mark_ok(key)
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = None
                try:
                    retry_after = int(e.response.headers.get("Retry-After", 0))
                except (ValueError, TypeError):
                    pass
                _gemini_mark_limited(key, retry_after)
                continue
            raise

    raise RuntimeError(
        f"Tất cả {len(available)} Gemini keys khả dụng đều bị 429. Fallback về OpenRouter."
    )


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
