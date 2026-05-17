import json
import logging
import re
import time
from datetime import datetime
from threading import Lock
from typing import Optional
import httpx
from sqlalchemy.orm import Session
from ..models import AppSetting

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OR_MODEL    = "meta-llama/llama-3.3-70b-instruct:free"

# Danh sách fallback cứng — dùng khi chưa refresh từ API
FREE_MODELS = [
    # ── Router tự động chọn model miễn phí ──────────────────────────────
    {"id": "openrouter/free", "name": "⚡ Auto Free Router (tự động chọn model tốt nhất)"},

    # ── Các model lớn, khuyến nghị viết bài ─────────────────────────────
    {"id": DEFAULT_OR_MODEL, "name": "Llama 3.3 70B Instruct — Khuyến nghị (131K ctx)"},
    {"id": "nvidia/nemotron-3-super-120b-a12b:free", "name": "NVIDIA Nemotron 3 Super 120B (1M ctx)"},
    {"id": "openai/gpt-oss-120b:free", "name": "OpenAI GPT OSS 120B (131K ctx)"},
    {"id": "openai/gpt-oss-20b:free", "name": "OpenAI GPT OSS 20B (131K ctx)"},
    {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder 480B (1M ctx)"},
    {"id": "qwen/qwen3-next-80b-a3b-instruct:free", "name": "Qwen3 Next 80B A3B (262K ctx)"},
    {"id": "inclusionai/ling-2.6-1t:free", "name": "InclusionAI Ling-2.6-1T (262K ctx)"},
    {"id": "tencent/hy3-preview:free", "name": "Tencent Hy3 Preview (262K ctx)"},
    {"id": "minimax/minimax-m2.5:free", "name": "MiniMax M2.5 (205K ctx)"},
    {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "name": "NVIDIA Nemotron Nano Omni 30B Reasoning (256K ctx)"},
    {"id": "nvidia/nemotron-3-nano-30b-a3b:free", "name": "NVIDIA Nemotron Nano 30B (256K ctx)"},
    {"id": "nousresearch/hermes-3-llama-3.1-405b:free", "name": "Nous Hermes 3 405B (131K ctx)"},

    # ── Google Gemini Free (OpenRouter) ─────────────────────────────────
    {"id": "google/gemini-2.0-flash-exp:free", "name": "Gemini 2.0 Flash Exp — FREE (1M ctx) ⭐"},
    {"id": "google/gemini-flash-1.5:free",     "name": "Gemini 1.5 Flash — FREE (1M ctx)"},
    {"id": "google/gemini-flash-1.5-8b:free",  "name": "Gemini 1.5 Flash 8B — FREE (1M ctx)"},

    # ── Google Gemma ─────────────────────────────────────────────────────
    {"id": "google/gemma-4-31b-it:free", "name": "Google Gemma 4 31B (262K ctx)"},
    {"id": "google/gemma-4-26b-a4b-it:free", "name": "Google Gemma 4 26B A4B (262K ctx)"},
    {"id": "google/gemma-3-27b-it:free", "name": "Google Gemma 3 27B (131K ctx)"},
    {"id": "google/gemma-3-12b-it:free", "name": "Google Gemma 3 12B (131K ctx)"},
    {"id": "google/gemma-3-4b-it:free", "name": "Google Gemma 3 4B (131K ctx)"},
    {"id": "google/gemma-3n-e4b-it:free", "name": "Google Gemma 3n 4B (32K ctx)"},
    {"id": "google/gemma-3n-e2b-it:free", "name": "Google Gemma 3n 2B (8K ctx)"},

    # ── Các model khác ───────────────────────────────────────────────────
    {"id": "z-ai/glm-4.5-air:free", "name": "GLM 4.5 Air (131K ctx)"},
    {"id": "nvidia/nemotron-nano-12b-v2-vl:free", "name": "NVIDIA Nemotron Nano 12B VL (128K ctx)"},
    {"id": "nvidia/nemotron-nano-9b-v2:free", "name": "NVIDIA Nemotron Nano 9B V2 (32K ctx)"},
    {"id": "poolside/laguna-m.1:free", "name": "Poolside Laguna M.1 (131K ctx)"},
    {"id": "poolside/laguna-xs.2:free", "name": "Poolside Laguna XS.2 (131K ctx)"},
    {"id": "meta-llama/llama-3.2-3b-instruct:free", "name": "Llama 3.2 3B (131K ctx)"},
    {"id": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "name": "Dolphin Mistral 24B Venice (32K ctx)"},
    {"id": "liquid/lfm-2.5-1.2b-thinking:free", "name": "LFM2.5-1.2B Thinking (32K ctx)"},
    {"id": "liquid/lfm-2.5-1.2b-instruct:free", "name": "LFM2.5-1.2B Instruct (32K ctx)"},
]

LANGUAGE_NAMES = {
    "vi": "Vietnamese",
    "en": "English",
    "de": "German",
    "fr": "French",
    "ko": "Korean",
    "ja": "Japanese",
    "es": "Spanish",
}

# ─── Multi-model rotation ────────────────────────────────────────────────────
# Mỗi bài viết dùng model khác nhau: bài 1→ModelA, bài 2→ModelB, bài 3→ModelC...
# Index lưu trong memory (reset khi restart server — chấp nhận được).

_user_rotation_index: dict[int, int] = {}  # user_id → next index in pool

# Các model trả phí tốt nhất để đưa vào pool, grouped by credential key
_ROTATION_PAID = {
    "gemini_api_keys": [
        "gemini:gemini-2.0-flash",
        "gemini:gemini-2.5-flash",
        "gemini:gemini-1.5-flash",
    ],
    "claude_api_key": [
        "claude:claude-3-5-haiku-20241022",
        "claude:claude-3-5-sonnet-20241022",
    ],
    "openai_api_key": [
        "openai:gpt-4o-mini",
        "openai:gpt-4o",
    ],
    "groq_api_key": [
        "groq:llama-3.3-70b-versatile",
        "groq:llama-3.1-8b-instant",
    ],
}

# Số model free lấy vào pool (top N theo thứ tự danh sách)
_ROTATION_FREE_LIMIT = 10


def _build_rotation_pool(db: Session, user_id: int = None) -> list[str]:
    """Xây dựng pool model cho rotation: paid (nếu có key) + free OpenRouter."""
    pool: list[str] = []

    # Paid providers — chỉ thêm nếu user đã cấu hình key
    for cred_key, models in _ROTATION_PAID.items():
        if get_setting(db, cred_key, user_id=user_id):
            pool.extend(models)

    # Free OpenRouter models — bỏ qua thinking models và auto-router
    if get_setting(db, "openrouter_api_key", user_id=user_id):
        free_ids = [
            m["id"] for m in get_current_free_models(db, user_id=user_id)
            if m["id"] not in _THINKING_MODELS and m["id"] != "openrouter/free"
        ]
        pool.extend(free_ids[:_ROTATION_FREE_LIMIT])

    return pool


def pick_rotation_model(db: Session, user_id: int = None) -> str:
    """
    Trả về model tiếp theo trong vòng xoay round-robin.
    Bài 1 → pool[0], bài 2 → pool[1], ..., hết pool → quay lại pool[0].
    Nếu pool rỗng (chưa cấu hình gì) → trả về model mặc định của project.
    """
    pool = _build_rotation_pool(db, user_id)
    if not pool:
        return ""   # caller dùng project.ai_model làm fallback

    uid = user_id or 0
    idx = _user_rotation_index.get(uid, 0) % len(pool)
    _user_rotation_index[uid] = idx + 1   # advance cho bài tiếp theo
    model = pool[idx]
    logger.info(f"[rotation] user={uid} bài #{idx + 1}/{len(pool)} → {model}")
    return model


# Model dạng "thinking/reasoning" — xuất chuỗi suy luận thay vì JSON thuần.
# Bị bỏ qua hoàn toàn khi json_mode=True (clustering, v.v.)
_THINKING_MODELS = {
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "liquid/lfm-2.5-1.2b-thinking:free",
}

# Lỗi cho biết model quá tải / không khả dụng → chuyển sang model khác
_FALLBACK_STATUS_CODES = {429, 500, 502, 503, 504, 524, 529}
_FALLBACK_ERROR_KEYWORDS = [
    "overloaded", "rate limit", "too many requests", "capacity",
    "unavailable", "no endpoints", "model not found", "error 429",
    "provider returned error", "524", "timeout", "timed out",
]

# ─── Circuit Breaker (in-memory model health) ─────────────────────────────────
# Track model nào đang trong cooldown sau lỗi.
# Reset khi app khởi động lại — đây là hành vi mong muốn.

_health_lock = Lock()
_model_failed_at: dict[str, float] = {}   # model_id → epoch time lần lỗi gần nhất
_model_fail_count: dict[str, int] = {}    # model_id → số lần lỗi liên tiếp

_COOLDOWN_BASE_SECS = 300   # 5 phút sau lần lỗi đầu
_COOLDOWN_MAX_SECS  = 1800  # tối đa 30 phút


def _cooldown_secs(fail_count: int) -> int:
    """Exponential backoff: 5m → 10m → 20m → 30m (capped)."""
    return min(_COOLDOWN_BASE_SECS * (2 ** (fail_count - 1)), _COOLDOWN_MAX_SECS)


def _model_is_available(model_id: str) -> bool:
    with _health_lock:
        failed_at = _model_failed_at.get(model_id)
        if failed_at is None:
            return True
        count = _model_fail_count.get(model_id, 1)
        return (time.time() - failed_at) > _cooldown_secs(count)


def _on_model_failure(model_id: str) -> None:
    with _health_lock:
        _model_failed_at[model_id] = time.time()
        _model_fail_count[model_id] = _model_fail_count.get(model_id, 0) + 1
        count = _model_fail_count[model_id]
        cooldown = _cooldown_secs(count)
        logger.warning(
            f"[circuit-breaker] {model_id} lỗi lần {count}, "
            f"cooldown {cooldown // 60} phút"
        )


def _on_model_success(model_id: str) -> None:
    with _health_lock:
        if model_id in _model_failed_at:
            _model_failed_at.pop(model_id)
            _model_fail_count.pop(model_id, None)
            logger.info(f"[circuit-breaker] {model_id} khôi phục, xóa cooldown")


def get_model_health_summary() -> list[dict]:
    """Trả về trạng thái health của tất cả model đang theo dõi (cho UI/log)."""
    with _health_lock:
        now = time.time()
        result = []
        for model_id, failed_at in _model_failed_at.items():
            count = _model_fail_count.get(model_id, 1)
            cooldown = _cooldown_secs(count)
            remaining = max(0, cooldown - (now - failed_at))
            result.append({
                "model_id": model_id,
                "fail_count": count,
                "available": remaining == 0,
                "cooldown_remaining_secs": int(remaining),
            })
        return result


def get_setting(db: Session, key: str, default: str = "", user_id: int = None) -> str:
    if user_id:
        row = db.query(AppSetting).filter(AppSetting.key == f"u{user_id}_{key}").first()
        return row.value if row else default
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str, user_id: int = None):
    actual_key = f"u{user_id}_{key}" if user_id else key
    row = db.query(AppSetting).filter(AppSetting.key == actual_key).first()
    if not value:  # empty → xóa setting
        if row:
            db.delete(row)
            db.commit()
        return
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=actual_key, value=value))
    db.commit()


# ─── Dynamic Model List ───────────────────────────────────────────────────────

def fetch_free_models_from_api(api_key: str) -> list[dict]:
    """Gọi OpenRouter API để lấy danh sách model miễn phí mới nhất."""
    with httpx.Client(timeout=20) as client:
        resp = client.get(
            f"{OPENROUTER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        all_models = resp.json().get("data", [])

    free = []
    # Thêm router đặc biệt đầu tiên
    free.append({"id": "openrouter/free", "name": "⚡ Auto Free Router (tự động chọn model tốt nhất)"})

    for m in all_models:
        model_id = m.get("id", "")
        pricing = m.get("pricing", {})
        prompt_price = str(pricing.get("prompt", "1"))
        completion_price = str(pricing.get("completion", "1"))

        # Model miễn phí: id kết thúc bằng :free HOẶC giá = 0
        is_free = (
            model_id.endswith(":free")
            or (prompt_price in ("0", "0.0") and completion_price in ("0", "0.0"))
        )
        if not is_free:
            continue

        ctx = m.get("context_length", 0)
        ctx_str = f"{ctx // 1000}K" if ctx >= 1000 else f"{ctx}"
        name = m.get("name", model_id)
        free.append({"id": model_id, "name": f"{name} ({ctx_str} ctx)"})

    return free


def get_current_free_models(db: Optional[Session] = None, user_id: int = None) -> list[dict]:
    """Trả về danh sách models từ DB nếu đã refresh, không thì dùng danh sách cứng."""
    if db is None:
        return FREE_MODELS

    cached = get_setting(db, "cached_free_models", user_id=user_id)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass
    return FREE_MODELS


def refresh_free_models(db: Session) -> tuple[list[dict], int]:
    """Cập nhật danh sách models từ OpenRouter API, lưu vào DB. Trả về (models, count)."""
    api_key = get_setting(db, "openrouter_api_key")
    if not api_key:
        raise ValueError("Chưa cấu hình OpenRouter API key")

    models = fetch_free_models_from_api(api_key)
    set_setting(db, "cached_free_models", json.dumps(models))
    return models, len(models)


# ─── Auto-Fallback Call ───────────────────────────────────────────────────────

def _is_fallback_error(exc: Exception) -> bool:
    """Kiểm tra lỗi có phải do model quá tải / không khả dụng không."""
    msg = str(exc).lower()
    if hasattr(exc, "response"):
        status = getattr(exc.response, "status_code", 0)
        if status in _FALLBACK_STATUS_CODES:
            return True
        try:
            body = exc.response.json()
            msg += json.dumps(body).lower()
        except Exception:
            pass
    return any(kw in msg for kw in _FALLBACK_ERROR_KEYWORDS)


def call_openrouter(api_key: str, model: str, messages: list, max_tokens: int = 4000, json_mode: bool = False) -> str:
    """Gọi một model cụ thể. Raise exception nếu lỗi."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://autoblogspot.com",
        "X-Title": "AutoBlogspot",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.8,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        payload["include_reasoning"] = False
    with httpx.Client(timeout=120) as client:
        resp = client.post(f"{OPENROUTER_BASE_URL}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        # Kiểm tra lỗi trong body (một số model trả 200 nhưng có error)
        if "error" in data:
            err = data["error"]
            raise httpx.HTTPStatusError(
                str(err), request=resp.request, response=resp
            )
        content = data["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("Model returned empty content")
        return content


def smart_call(
    db: Session,
    preferred_model: str,
    messages: list,
    max_tokens: int = 4000,
    user_id: int = None,
    json_mode: bool = False,
) -> tuple[str, str]:
    """
    Route AI call đến đúng provider dựa vào prefix model:
      claude:MODEL_ID  → Anthropic API
      openai:MODEL_ID  → OpenAI API
      groq:MODEL_ID    → Groq API
      (khác)           → OpenRouter với auto-fallback
    Nếu paid provider lỗi → tự fallback về OpenRouter.
    """
    from . import ai_providers

    if preferred_model and ":" in preferred_model:
        provider, model_id = preferred_model.split(":", 1)

        # ── Gemini (multi-key) ────────────────────────────────────────────────
        if provider == "gemini":
            keys_str = get_setting(db, "gemini_api_keys", user_id=user_id)
            if keys_str and keys_str.strip():
                try:
                    content = ai_providers.call_gemini(keys_str, model_id, messages, max_tokens)
                    return content, preferred_model
                except Exception as exc:
                    logger.warning(f"[smart_call] gemini:{model_id} lỗi ({exc}), fallback OpenRouter")
            else:
                logger.warning(f"[smart_call] Không có Gemini key (user_id={user_id}), fallback OpenRouter")

        # ── Claude / OpenAI / Groq (single key) ──────────────────────────────
        else:
            key_map  = {"claude": "claude_api_key", "openai": "openai_api_key", "groq": "groq_api_key"}
            call_map = {"claude": ai_providers.call_claude, "openai": ai_providers.call_openai, "groq": ai_providers.call_groq}
            if provider in key_map:
                api_key = get_setting(db, key_map[provider], user_id=user_id)
                if api_key:
                    try:
                        content = call_map[provider](api_key, model_id, messages, max_tokens)
                        return content, preferred_model
                    except Exception as exc:
                        logger.warning(f"[smart_call] {provider}:{model_id} lỗi ({exc}), fallback OpenRouter")
                else:
                    logger.warning(f"[smart_call] Không có key '{provider}' (user_id={user_id}), fallback OpenRouter")

    or_key = get_setting(db, "openrouter_api_key", user_id=user_id)
    # Strip provider prefix before passing to OpenRouter (e.g. "groq:model" → use default)
    or_model = preferred_model
    if preferred_model and ":" in preferred_model and not preferred_model.startswith("http"):
        or_model = get_setting(db, "openrouter_model", DEFAULT_OR_MODEL, user_id=user_id)
    return call_with_fallback(db, or_key, or_model, messages, max_tokens, json_mode=json_mode)


def call_with_fallback(
    db: Session,
    api_key: str,
    preferred_model: str,
    messages: list,
    max_tokens: int = 4000,
    json_mode: bool = False,
) -> tuple[str, str]:
    """
    Gọi AI với circuit breaker + auto-fallback 2 phase:
      Phase 1 — chỉ thử model khả dụng (không cooldown): preferred trước, rồi fallback.
      Phase 2 — nếu phase 1 thất bại hết, thử tất cả model đang cooldown làm cứu cánh cuối.
    Model bị skip trong phase 1 được thử lại trong phase 2 thay vì bị bỏ hoàn toàn.
    Trả về (content, model_used).
    """
    models_list = get_current_free_models(db)
    all_fallback_ids = [
        m["id"] for m in models_list
        if m["id"] not in ("openrouter/free", preferred_model)
    ]

    # If preferred_model is the "auto" sentinel, replace it with first real fallback
    if preferred_model == "openrouter/free":
        preferred_model = all_fallback_ids[0] if all_fallback_ids else DEFAULT_OR_MODEL
        all_fallback_ids = [m for m in all_fallback_ids if m != preferred_model]

    # Loại bỏ thinking model khỏi toàn bộ fallback chain —
    # chúng xuất reasoning text thay vì content, phá hỏng cả JSON lẫn bài viết
    full_order = [preferred_model] + all_fallback_ids
    skipped = [m for m in full_order if m in _THINKING_MODELS]
    full_order = [m for m in full_order if m not in _THINKING_MODELS]
    if skipped:
        logger.info(f"[fallback] Bỏ qua thinking models: {skipped}")

    phase1, phase2 = [], []
    for m in full_order:
        (phase1 if _model_is_available(m) else phase2).append(m)

    if preferred_model in phase2:
        count = _model_fail_count.get(preferred_model, 1)
        logger.info(
            f"[circuit-breaker] '{preferred_model}' đang cooldown "
            f"({count} lỗi liên tiếp), dùng fallback trước"
        )

    last_error = None

    for phase, candidates in ((1, phase1), (2, phase2)):
        if not candidates:
            continue
        if phase == 2:
            logger.warning(
                f"[circuit-breaker] Phase 1 thất bại hết, thử {len(candidates)} "
                f"model đang cooldown làm cứu cánh cuối"
            )
        for model_id in candidates:
            try:
                content = call_openrouter(api_key, model_id, messages, max_tokens, json_mode=json_mode)
                _on_model_success(model_id)
                if model_id != preferred_model:
                    logger.warning(f"[fallback] {preferred_model} → {model_id}")
                return content, model_id
            except Exception as exc:
                if _is_fallback_error(exc):
                    _on_model_failure(model_id)
                    last_error = exc
                    continue
                raise  # lỗi khác (network, JSON parse…) → raise ngay

    raise RuntimeError(
        f"Tất cả free models đều không khả dụng. Lỗi cuối: {last_error}"
    )


# ─── Anti-AI Detection ───────────────────────────────────────────────────────

# Cặp (pattern, replacement) — thay thế từ/cụm từ AI điển hình
_AI_PHRASE_MAP = [
    # ── Tiếng Anh ─────────────────────────────────────────────────────────
    (r"\bFurthermore,",          "And —"),
    (r"\bMoreover,",             "What's more,"),
    (r"\bAdditionally,",         "Plus,"),
    (r"\bIn addition,",          "Also,"),
    (r"\bIt is worth noting that\b", "Worth knowing:"),
    (r"\bIt is important to note that\b", "Keep in mind:"),
    (r"\bIt should be noted that\b",     "Note:"),
    (r"\bIn conclusion,",        "So,"),
    (r"\bTo summarize,",         "In short,"),
    (r"\bTo conclude,",          "To wrap up,"),
    (r"\bIn today's fast-paced world\b", "These days"),
    (r"\bIn today's world\b",    "Today"),
    (r"\bNeedless to say,",      ""),
    (r"\bIt goes without saying that\b", ""),
    (r"\bSignificantly,",        ""),
    (r"\bNotably,",              ""),
    (r"\bLeverage\b",            "use"),
    (r"\bleverage\b",            "use"),
    (r"\bUtilize\b",             "use"),
    (r"\butilize\b",             "use"),
    (r"\bDelve into\b",          "explore"),
    (r"\bdelve into\b",          "explore"),
    (r"\bComprehensive guide\b", "complete guide"),
    (r"\bThis article (will |aims to )?explore\b", "Here we explore"),
    (r"\bIn this article, we will\b", "Here,"),
    (r"\bThis article aims to\b",     "Here"),
    # ── Tiếng Việt ────────────────────────────────────────────────────────
    (r"\bHơn nữa,",              "Thêm vào đó,"),
    (r"\bBên cạnh đó,",          "Ngoài ra,"),
    (r"Điều quan trọng cần lưu ý là", "Một điều cần nhớ:"),
    (r"Có thể nói rằng",         "Thực ra,"),
    (r"Tóm lại,",                "Nói gọn lại,"),
    (r"Để tóm tắt,",             "Nói ngắn gọn,"),
    (r"Nhìn chung,",             "Về cơ bản,"),
    (r"Cần lưu ý rằng",          "Chú ý:"),
    (r"Điều này cho thấy rằng",  "Điều đó có nghĩa là"),
    (r"Trong bài viết này",      "Ở đây"),
    (r"Bài viết này sẽ",         "Dưới đây"),
    (r"Để kết luận,",            "Nói thẳng ra,"),
    (r"Kết luận lại,",           "Tóm lại,"),
    (r"Một điều đáng chú ý là",  "Đáng chú ý:"),
]


def _replace_ai_phrases(html: str) -> str:
    """Tầng 1: thay thế các cụm từ AI điển hình bằng cách diễn đạt tự nhiên hơn."""
    for pattern, replacement in _AI_PHRASE_MAP:
        html = re.sub(pattern, replacement, html)
    # Dọn khoảng trắng thừa sau khi xóa một số cụm
    html = re.sub(r"  +", " ", html)
    return html


def humanize_article(
    db: Session,
    content: str,
    language: str,
    model: Optional[str] = None,
    user_id: int = None,
) -> str:
    """
    Tầng 2: gọi AI để viết lại bài theo giọng người thật.
    Giữ nguyên toàn bộ HTML, thực tế, liên kết — chỉ thay đổi phong cách viết.
    Nếu lỗi → trả về content gốc (không làm hỏng bài).
    """
    preferred = model or get_setting(db, "openrouter_model", DEFAULT_OR_MODEL, user_id=user_id)
    lang_name = LANGUAGE_NAMES.get(language, "English")

    prompt = f"""You are a professional human editor. Your job is to rewrite the article below so it sounds 100% natural — as if written by an experienced human blogger, not an AI.

=== STRICT RULES (MUST follow) ===
1. Keep ALL facts, data, and information exactly the same — do NOT add or remove information
2. Keep ALL HTML tags intact: h2, h3, p, ul, ol, li, strong, em, blockquote, figure, img — do NOT delete any tags
3. Keep ALL <a href="..."> links exactly as they are (backlinks and internal links)
4. Language must remain entirely in {lang_name}
5. Keep the same article structure (same number of sections and headings)

=== REWRITING STYLE (make it feel genuinely human) ===
• Sentence length variety: alternate between short punchy sentences (4–7 words) and longer ones (20–30 words) within the same paragraph. Never write 4+ sentences of the same length in a row.
• Paragraph length variety: some paragraphs = 1–2 sentences, others = 4–5 sentences. Vary it.
• Kill robotic phrases: replace any "Furthermore", "Moreover", "In addition", "It is worth noting", "Needless to say", "Delve into", "Leverage", "Utilize", "In conclusion", "To summarize" with natural casual alternatives.
• Natural transitions: use "Here's the thing —", "And that's exactly why...", "Honestly,", "The truth is,", "But here's what most people miss:", "Sound familiar?", "Good question."
• Add personality: include 1–2 first-person lines ("In my experience...", "I've seen this work when..."), a mild opinion ("Personally, I'd recommend..."), or light casual humor.
• Rhetorical questions: ask the reader 2–3 questions throughout the article ("Sound familiar?", "So what does that mean for you?", "Why does this matter?").
• Emotional tone: express genuine enthusiasm about good tips, mild empathy for the reader's problem, or light frustration about common mistakes.
• Imperfect structure: not every sentence needs to be grammatically perfect — occasional informal constructions are fine.

=== ARTICLE TO REWRITE ===
{content}

Return ONLY the rewritten HTML — no explanation, no JSON, no markdown fence, just the HTML content starting from the first tag."""

    try:
        result, used_model = smart_call(
            db, preferred,
            [{"role": "user", "content": prompt}],
            max_tokens=5000,
            user_id=user_id,
        )
        logger.info(f"humanize_article used model: {used_model}")

        result = result.strip()
        # Bỏ markdown code block nếu AI vẫn trả về
        if result.startswith("```"):
            for part in result.split("```"):
                cleaned = part.replace("html", "").strip()
                if cleaned.startswith("<"):
                    result = cleaned
                    break

        return result if result and "<" in result else content

    except Exception as e:
        logger.warning(f"humanize_article failed (using original): {e}")
        return content


class TruncatedResponseError(ValueError):
    """Raised when the AI response was cut off before the JSON was complete."""
    def __init__(self, message: str, partial_raw: str = ""):
        super().__init__(message)
        self.partial_raw = partial_raw


# ─── JSON Parsing ────────────────────────────────────────────────────────────

def _repair_truncated_json(text: str) -> str:
    """
    Cố gắng sửa JSON bị cắt giữa chừng do model hết output token.
    Chiến lược: cắt bỏ chuỗi/value cuối chưa hoàn thành, đóng các bracket còn mở.
    """
    # Tìm dấu phẩy hoặc kết thúc value cuối cùng hợp lệ
    # Cắt bỏ phần trailing chưa hoàn chỉnh bằng cách tìm vị trí an toàn
    depth = 0
    in_string = False
    escape_next = False
    last_safe = 0

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            depth += 1
        elif ch in ('}', ']'):
            depth -= 1
            if depth == 0:
                last_safe = i + 1
        elif ch == ',' and depth == 1:
            last_safe = i  # có thể cắt ngay trước dấu phẩy cuối

    if last_safe > 0 and last_safe < len(text):
        # Cắt tại vị trí an toàn cuối cùng rồi đóng bracket
        truncated = text[:last_safe].rstrip().rstrip(',')
        # Đếm bracket chưa đóng
        opens = {'[': ']', '{': '}'}
        stack = []
        in_str = False
        esc = False
        for ch in truncated:
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in opens:
                stack.append(opens[ch])
            elif ch in (']', '}') and stack:
                stack.pop()
        closing = ''.join(reversed(stack))
        return truncated + closing

    return text


def _extract_json(raw: str) -> dict | list:
    """
    Trích xuất JSON từ response AI, xử lý các trường hợp:
    - Có markdown code block ```json ... ```
    - Có ký tự điều khiển (literal newline/tab) bên trong chuỗi JSON
    - JSON bị bọc bởi text thừa
    - JSON bị cắt giữa chừng do model hết token
    """
    if not raw or not raw.strip():
        raise ValueError("AI returned empty response")

    text = raw.strip()

    # Bỏ markdown code block nếu có
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{") or part.startswith("["):
                text = part
                break

    # Cắt từ { hoặc [ đầu tiên đến } hoặc ] cuối cùng
    start = min(
        (text.find("{") if "{" in text else len(text)),
        (text.find("[") if "[" in text else len(text)),
    )
    end_brace = text.rfind("}")
    end_bracket = text.rfind("]")
    end = max(end_brace, end_bracket)

    if start <= end and start < len(text):
        text = text[start:end + 1]

    # Thử parse trực tiếp
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Xóa ký tự điều khiển không hợp lệ (giữ lại \n \r \t đã được escape)
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text)
    # Thay literal newline/tab bên trong chuỗi JSON bằng escaped version
    cleaned = re.sub(r'(?<=: ")(.*?)(?="[,\n\}])', lambda m: m.group(0).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'), cleaned, flags=re.DOTALL)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # JSON bị cắt giữa chừng — thử repair bằng cách đóng bracket
    try:
        repaired = _repair_truncated_json(raw.strip())
        if repaired != raw.strip():
            return json.loads(repaired)
    except (json.JSONDecodeError, Exception):
        pass

    try:
        repaired_cleaned = _repair_truncated_json(cleaned)
        if repaired_cleaned != cleaned:
            return json.loads(repaired_cleaned)
    except (json.JSONDecodeError, Exception):
        pass

    # Phát hiện truncation: response không kết thúc bằng } hoặc ]
    stripped_end = raw.strip()[-1] if raw.strip() else ""
    is_truncated = stripped_end not in ("}", "]")
    if is_truncated:
        raise TruncatedResponseError(
            f"AI response was truncated (ends with '{stripped_end}')\nRaw (first 500 chars): {raw[:500]}",
            partial_raw=raw,
        )

    raise ValueError(f"Cannot parse AI response as JSON\nRaw (first 500 chars): {raw[:500]}")


# ─── Public AI Functions ──────────────────────────────────────────────────────

def _continue_truncated_response(
    db: Session,
    prior_messages: list[dict],
    partial_raw: str,
    preferred: str,
    user_id: int = None,
    max_tokens: int = 4000,
    close_with: str = "}",
) -> Optional[dict | list]:
    """
    Khi AI response bị cắt giữa JSON, gửi continuation prompt để model viết tiếp.
    prior_messages: toàn bộ lịch sử hội thoại gốc (system + user messages).
    close_with: ký tự đóng JSON cuối cùng ("}" cho object, "]" cho array).
    Trả về kết quả đã parse, hoặc None nếu thất bại.
    """
    partial_stripped = partial_raw.rstrip()
    continuation_messages = list(prior_messages) + [
        {"role": "assistant", "content": partial_stripped},
        {
            "role": "user",
            "content": (
                f"Your previous response was cut off before the JSON was complete. "
                f"Continue from EXACTLY where you stopped — output ONLY the remaining text "
                f"(do NOT repeat anything already written). "
                f"Close all open strings, arrays, and objects so the result is valid JSON ending with {close_with}."
            ),
        },
    ]
    try:
        continuation, used_model = smart_call(
            db, preferred, continuation_messages, max_tokens=max_tokens, user_id=user_id
        )
        logger.info(f"_continue_truncated_response model: {used_model}")
        merged = partial_stripped + continuation.strip()
        return _extract_json(merged)
    except Exception as e:
        logger.warning(f"_continue_truncated_response failed: {e}")
        return None


_CLUSTER_BATCH_SIZE = 60  # từ khóa tối ưu mỗi lô


def analyze_search_intent_and_research(
    db: Session,
    keywords: list[str],
    cluster_name: str,
    language: str,
    model: Optional[str] = None,
    user_id: int = None,
) -> dict:
    """
    Bước 1 của pipeline viết bài:
    AI phân tích search intent + tạo research brief cho topic.
    Kết quả được cache trong cluster.intent_analysis và tái dùng cho các bài cùng cluster.
    """
    preferred = model or get_setting(db, "openrouter_model", DEFAULT_OR_MODEL, user_id=user_id)
    lang_name = LANGUAGE_NAMES.get(language, "English")
    kw_str = ", ".join(keywords)

    prompt = f"""You are a senior SEO researcher and content strategist. Analyze the keyword cluster below and produce a research brief that an article writer will use to create expert-level content.

TOPIC CLUSTER: {cluster_name}
KEYWORDS: {kw_str}
CONTENT LANGUAGE: {lang_name}

## Task 1 — Search Intent Analysis
Determine what users REALLY want when they search these keywords on Google:
- intent_type: one of "informational" | "commercial" | "transactional" | "navigational"
- target_audience: who is searching this (age group, knowledge level, situation they are in)
- user_questions: the 5–8 most common questions users have about this topic
- user_pain_points: what problems, frustrations, or unmet needs drive this search
- search_context: why are they searching NOW (urgency, decision stage, triggering event)
- expected_format: content type that best satisfies this intent (step-by-step guide / comparison / FAQ / how-to / listicle / in-depth analysis / etc.)

## Task 2 — Research Brief
Generate the knowledge base a human expert would draw on to write this article:
- key_facts: 5–8 important facts, definitions, or core principles about this topic (be specific, not generic)
- statistics: 3–5 concrete data points or statistics relevant to this topic (approximate figures are fine if exact data unavailable)
- expert_insights: 3–4 practitioner-level insights that only someone with real experience would know
- common_mistakes: 3–5 mistakes beginners typically make with this topic
- subtopics: 6–8 subtopics the article must cover, ordered by relevance to the user's intent
- must_answer: the specific questions the article MUST answer to fully satisfy the searcher

Return ONLY valid JSON — no markdown fences, no explanation, nothing else:
{{
  "intent_type": "informational",
  "target_audience": "...",
  "user_questions": ["...", "..."],
  "user_pain_points": ["...", "..."],
  "search_context": "...",
  "expected_format": "...",
  "key_facts": ["...", "..."],
  "statistics": ["...", "..."],
  "expert_insights": ["...", "..."],
  "common_mistakes": ["...", "..."],
  "subtopics": ["...", "..."],
  "must_answer": ["...", "..."]
}}"""

    content, used_model = smart_call(
        db, preferred,
        [{"role": "user", "content": prompt}],
        max_tokens=2000,
        user_id=user_id,
    )
    logger.info(f"research_brief ({cluster_name[:50]}) used model: {used_model}")
    return _extract_json(content)


def _format_research_context(brief: dict) -> str:
    """Convert a research brief dict into a structured prompt section."""
    lines = [
        "## RESEARCH CONTEXT (pre-analyzed — build your article on this foundation, do not re-analyze)",
        "",
    ]

    if brief.get("intent_type"):
        lines.append(f"**Search Intent**: {brief['intent_type']}")
    if brief.get("target_audience"):
        lines.append(f"**Target Audience**: {brief['target_audience']}")
    if brief.get("search_context"):
        lines.append(f"**Why they're searching**: {brief['search_context']}")
    if brief.get("expected_format"):
        lines.append(f"**Best content format**: {brief['expected_format']}")

    if brief.get("user_questions"):
        lines.append("\n**Questions users want answered** (your article MUST address all of these):")
        for q in brief["user_questions"]:
            lines.append(f"  - {q}")

    if brief.get("user_pain_points"):
        lines.append("\n**User pain points to address**:")
        for p in brief["user_pain_points"]:
            lines.append(f"  - {p}")

    if brief.get("key_facts"):
        lines.append("\n**Key facts to incorporate** (weave naturally into the content):")
        for f in brief["key_facts"]:
            lines.append(f"  - {f}")

    if brief.get("statistics"):
        lines.append("\n**Statistics to reference** (cite in context, not as a bare list):")
        for s in brief["statistics"]:
            lines.append(f"  - {s}")

    if brief.get("expert_insights"):
        lines.append("\n**Expert insights** (present as your own knowledge and experience):")
        for ins in brief["expert_insights"]:
            lines.append(f"  - {ins}")

    if brief.get("common_mistakes"):
        lines.append("\n**Common mistakes to warn readers about** (adds genuine value):")
        for m in brief["common_mistakes"]:
            lines.append(f"  - {m}")

    if brief.get("subtopics"):
        lines.append("\n**Required subtopics** (structure your H2 sections around these, in priority order):")
        for idx, t in enumerate(brief["subtopics"], 1):
            lines.append(f"  {idx}. {t}")

    if brief.get("must_answer"):
        lines.append("\n**Questions the article MUST answer** (use as H3 sub-sections or FAQ entries):")
        for q in brief["must_answer"]:
            lines.append(f"  - {q}")

    return "\n".join(lines)


def _cluster_batch(
    db: Session,
    keywords: list[str],
    preferred: str,
    user_id: int = None,
) -> list[dict]:
    """Gọi AI phân cụm cho một lô từ khóa. Retry tối đa 3 lần nếu AI không trả JSON."""
    kw_list = "\n".join(f"- {kw}" for kw in keywords)
    prompt = f"""You are a senior SEO strategist. Group the following keywords into topical clusters. Each cluster will become ONE SEO article.

Keywords to cluster:
{kw_list}

Rules:
1. Each cluster has exactly 1 PRIMARY keyword and 0–5 SECONDARY keywords (you decide how many based on relevance).
2. PRIMARY keyword: the most specific, highest-intent keyword that best represents the article topic.
3. SECONDARY keywords: closely related variations or long-tail versions of the primary keyword that can be naturally woven into the same article. Only add if they truly belong — do NOT force unrelated keywords together.
4. Group by SEARCH INTENT — all keywords in a cluster must share the same intent (informational, commercial, or transactional).
5. Cluster "name" must be a specific, article-ready topic title (e.g. "How to Choose Outdoor Park Benches for Small Gardens", not "Park Bench").
6. Every keyword must appear in exactly one cluster.

Return ONLY a valid JSON array — no explanation, no markdown, nothing else:
[
  {{
    "name": "Specific SEO Article Topic",
    "primary_keyword": "the single most important keyword",
    "secondary_keywords": ["variation 1", "long-tail variant"]
  }}
]"""

    system_msg = {"role": "system", "content": "You are a JSON API. Output ONLY valid JSON. No explanation, no thinking text, no markdown. Start with [ and end with ]."}
    retry_prompt = (
        "Output ONLY the raw JSON array below. "
        "No explanation, no thinking, no markdown. Start your response with [ and end with ].\n\n"
        + prompt
    )

    def _normalize_clusters(raw: list) -> list[dict]:
        normalized = []
        for c in raw:
            primary = c.get("primary_keyword", "")
            secondary = c.get("secondary_keywords") or c.get("keywords") or []
            if primary and primary not in secondary:
                kws = [primary] + [k for k in secondary if k]
            else:
                kws = secondary or ([primary] if primary else [])
            normalized.append({"name": c.get("name", ""), "keywords": kws})
        return normalized

    last_error = None
    for attempt in range(3):
        try:
            use_prompt = retry_prompt if attempt > 0 else prompt
            cluster_messages = [system_msg, {"role": "user", "content": use_prompt}]
            content, used_model = smart_call(
                db, preferred, cluster_messages,
                max_tokens=3000,
                user_id=user_id,
                json_mode=True,
            )
            logger.info(f"cluster_batch ({len(keywords)} kws) used model: {used_model} attempt={attempt + 1}")
            raw = _extract_json(content)
            return _normalize_clusters(raw)

        except TruncatedResponseError as e:
            logger.warning(f"cluster_batch truncated at attempt {attempt + 1} ({len(e.partial_raw)} chars) — trying continuation")
            use_prompt = retry_prompt if attempt > 0 else prompt
            cluster_messages = [system_msg, {"role": "user", "content": use_prompt}]
            result = _continue_truncated_response(
                db, cluster_messages, e.partial_raw, preferred,
                user_id=user_id, max_tokens=2000, close_with="]",
            )
            if result is not None:
                logger.info(f"cluster_batch continuation succeeded ({len(result)} clusters)")
                return _normalize_clusters(result)
            last_error = e
            logger.warning(f"cluster_batch continuation also failed — attempt {attempt + 1} abandoned")

        except ValueError as e:
            last_error = e
            logger.warning(f"cluster_batch attempt {attempt + 1} failed (JSON parse error): {e}")

    raise last_error


def cluster_keywords(db: Session, keywords: list[str], model: Optional[str] = None, user_id: int = None) -> list[dict]:
    """
    Phân cụm từ khóa theo chủ đề bằng AI.
    Tự động chia lô nếu số từ khóa > _CLUSTER_BATCH_SIZE để đảm bảo chất lượng.
    """
    preferred = model or get_setting(db, "openrouter_model", DEFAULT_OR_MODEL, user_id=user_id)

    # Chia thành các lô nếu quá nhiều từ khóa
    if len(keywords) <= _CLUSTER_BATCH_SIZE:
        return _cluster_batch(db, keywords, preferred, user_id=user_id)

    all_clusters = []
    for i in range(0, len(keywords), _CLUSTER_BATCH_SIZE):
        batch = keywords[i: i + _CLUSTER_BATCH_SIZE]
        logger.info(f"Clustering batch {i // _CLUSTER_BATCH_SIZE + 1}: {len(batch)} keywords")
        clusters = _cluster_batch(db, batch, preferred, user_id=user_id)
        all_clusters.extend(clusters)

    return all_clusters


def analyze_intent_and_write_article(
    db: Session,
    keywords: list[str],
    cluster_name: str,
    language: str,
    backlinks: list[dict],
    model: Optional[str] = None,
    existing_titles: list[str] = None,
    internal_links: list[dict] = None,
    author_persona: Optional[dict] = None,
    content_angle: Optional[dict] = None,
    research_brief: Optional[dict] = None,
    user_id: int = None,
) -> dict:
    """Viết bài unique từ research brief (nếu có) hoặc tự phân tích intent."""
    preferred = model or get_setting(db, "openrouter_model", DEFAULT_OR_MODEL, user_id=user_id)
    lang_name = LANGUAGE_NAMES.get(language, "English")
    kw_str = ", ".join(keywords)
    now = datetime.now()
    current_date_str = now.strftime("%B %Y")   # e.g. "May 2026"
    current_year = now.year

    backlink_str = ""
    if backlinks:
        lines = "\n".join(
            f'- <a href="{bl.get("url", "")}">{bl.get("anchor", "")}</a>'
            for bl in backlinks
        )
        backlink_str = (
            f"\n## MANDATORY BACKLINKS\n"
            f"You MUST insert ALL of the following links into the article body as HTML anchor tags exactly as shown. "
            f"Place each one inside a relevant sentence — do NOT list them separately, do NOT skip any:\n{lines}"
        )

    existing_str = ""
    if existing_titles:
        existing_str = (
            "\nIMPORTANT: These titles already exist for this topic - "
            "your article MUST have a completely different angle and title:\n"
            + "\n".join(f"- {t}" for t in existing_titles)
        )

    internal_link_str = ""
    if internal_links:
        lines = "\n".join(
            f'- <a href="{il.get("url", "")}">{il.get("title", "")}</a>'
            for il in internal_links
        )
        internal_link_str = (
            "\n## Internal Links\n"
            "Naturally link to relevant existing articles on the same website "
            "by inserting the anchor tags below where they fit contextually "
            "(do NOT force links that are irrelevant):\n"
            + lines
        )

    # Author persona intro
    if author_persona:
        persona_intro = (
            f"You are {author_persona['name']} — {author_persona['bio']}\n\n"
            f"Your natural writing style: {author_persona['writing_style']}\n\n"
            f"Write entirely in character as this person. Not as a generic AI assistant."
        )
    else:
        persona_intro = (
            "You are a human expert blogger with 10+ years of real-world experience "
            "in this topic. Write exactly as a knowledgeable human would — not as an AI assistant."
        )

    # Content angle section
    angle_section = ""
    if content_angle:
        angle_section = (
            f"\n## CONTENT ANGLE (MANDATORY)\n"
            f"**Angle**: {content_angle['name']}\n"
            f"**Approach**: {content_angle['description']}\n\n"
            f"Structure the ENTIRE article through this angle. "
            f"Title, intro, all sections, and conclusion must serve this specific approach.\n"
        )

    # Build the intent/research section of the prompt
    if research_brief:
        intent_section = _format_research_context(research_brief)
    else:
        intent_section = (
            f"## STEP 1 — Search Intent Analysis\n"
            f"Topic cluster: {cluster_name}\n"
            f"Keywords: {kw_str}\n"
            f"Identify:\n"
            f"- Primary intent: what the user REALLY wants (learn / compare / buy / solve a problem)\n"
            f"- Key pain points or questions behind these keywords\n"
            f"- What the ideal article must deliver to satisfy this intent fully"
        )

    prompt = f"""{persona_intro}

TODAY'S DATE: {current_date_str}
IMPORTANT: All content must reflect {current_year} as the current year. Do NOT cite 2025 data as "current", "this year", or "recently" — that is outdated. Use {current_year} figures, trends, and context throughout.

{intent_section}
{existing_str}
{angle_section}
## Write a Complete Article in {lang_name}
{backlink_str}
{internal_link_str}

### Article structure (MANDATORY):
1. **Title** — Specific, click-worthy. Include the primary keyword + a power element (number, "Guide", "Proven", "Step-by-Step"). Avoid generic titles.
2. **Answer Box** ⭐ GEO/AIO — Immediately after the title, write a `<p>` paragraph of 50–60 words that directly answers the primary search query. No preamble. No "In this article...". Just the direct answer. Google uses this for AI Overviews; AI assistants use this as a citation snippet.
3. **Introduction (hook)** — After the Answer Box, write 2–3 paragraphs with a relatable scenario or surprising fact. State what the reader will gain.
4. **Body sections** — 4–6 H2 sections. Each covers one focused sub-topic. Use H3 for sub-points. Include at least one: numbered steps, comparison, checklist, or real example. **Each section MUST contain at least 1 specific fact, number, or statistic** (e.g. "studies show X%", "as of {current_year}, Y...") — vague generalities are not acceptable.
5. **FAQ section** ⭐ AIO — 4–5 questions in the exact phrasing users type into Google or ask AI assistants. Each answer: 2–3 concise sentences, factual, self-contained (readable without context). Use `<h3>` for questions, `<p>` for answers. This section is critical for Google AI Overviews.
6. **Conclusion** — Brief takeaways + clear CTA.

### Writing style — CRITICAL (human voice, bypass AI detectors):
- **Sentence variety**: Mix very short sentences (3–6 words) with longer ones (20–30 words). Never write 5 sentences of the same length in a row.
- **Burstiness**: Some paragraphs are 1–2 sentences. Others are 4–5. Vary deliberately.
- **Forbidden AI phrases**: NEVER use "Furthermore", "Moreover", "In addition", "It is worth noting", "It is important to note", "In conclusion", "To summarize", "Delve into", "Leverage", "Utilize" — these are instant AI red flags.
- **Natural transitions**: Use casual connectors instead — "Here's the thing.", "And that's exactly why...", "But wait —", "The good news?", "Honestly,", "Let me explain."
- **Personal voice**: Include 1–2 first-person observations ("In my experience...", "I've seen this go wrong when..."), mild opinions ("Personally, I think..."), or light humor.
- **Imperfect structure**: Not every section needs to be the same length. One section can be 80 words, another 300 words — just like a real writer would do.
- **Concrete specifics**: Use real-sounding examples, specific numbers, named scenarios. Avoid vague generalities.
- **Rhetorical questions**: Ask the reader 2–3 questions throughout ("Sound familiar?", "So what does that mean for you?").
- **Emotional resonance**: Express genuine enthusiasm, mild frustration, or empathy where appropriate.
- Language: {lang_name} ONLY — use natural idioms, colloquial expressions, and phrases native speakers actually use.

### Technical requirements:
- Length: 1200–2000 words
- Keyword "{kw_str}" woven in naturally — never forced or repeated unnaturally
- HTML tags only: h2, h3, p, ul, ol, li, strong, em, blockquote — NO html/head/body tags

### GEO/AIO optimization (MANDATORY):
- The Answer Box paragraph must be the FIRST `<p>` tag in the content — before any h2
- Every factual claim should be specific: use exact numbers, years, percentages
- FAQ answers must be self-contained: each answer must make sense when read alone, out of context
- Avoid filler phrases like "great question", "it depends", "there are many factors" in FAQ answers

## STEP 3 — Image Suggestions (in English)
- image_prompt: One VIVID, highly specific sentence for an AI image generator. CRITICAL RULES to avoid anatomy defects: (1) NEVER describe a full-body person — use "waist-up portrait", "head and shoulders close-up", or "face and chest only"; (2) PREFER scene/object/environment shots when possible — e.g., a product on a desk, a landscape, tools, food, technology devices; (3) If a person must appear, show them from the back, side, or very close-up face only. Describe setting, mood, lighting vividly. Example: "Waist-up portrait of a professional woman reviewing charts on a laptop screen, warm office lighting, shallow depth of field, photorealistic."
- image_queries: 3–5 short English keyword phrases (2–4 words each) for stock photo searches, one per main H2 section. Make them concrete and visual — avoid abstract terms.

## STEP 4 — Article Labels (in {lang_name})
Generate 1–3 concise blog category labels in {lang_name}.
Labels must be short broad topic words (e.g. "Sức khỏe", "Dinh dưỡng", "Thể thao").
Do NOT use the article title as a label. Max 30 characters each.

## STEP 5 — Structured Schema Markup (JSON-LD for GEO/AIO)
Generate structured data so AI assistants (ChatGPT, Perplexity, Google AI Overviews) can cite this article accurately.
Produce a JSON array with exactly 2 objects:
1. FAQPage: extract every Q&A pair from the FAQ section
2. Article: headline + description (use the Answer Box text) + date + language

Return ONLY valid JSON — no markdown fences, no extra text, nothing before or after the JSON:
{{
  "intent_analysis": "1–2 sentence summary of search intent and what the article delivers (in English)",
  "title": "SEO-optimized article title in {lang_name}",
  "content": "Complete HTML article in {lang_name} (all sections including FAQ and conclusion)",
  "image_prompt": "Vivid, specific English scene description for AI image generation",
  "image_queries": ["concrete visual query 1", "concrete visual query 2", "concrete visual query 3", "concrete visual query 4"],
  "labels": ["Nhãn 1", "Nhãn 2"],
  "schema_markup": [
    {{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{{"@type":"Question","name":"Câu hỏi 1?","acceptedAnswer":{{"@type":"Answer","text":"Câu trả lời 1."}}}}]}},
    {{"@context":"https://schema.org","@type":"Article","headline":"Tiêu đề bài viết","description":"Đoạn Answer Box 50-60 chữ","datePublished":"{current_date_str}","inLanguage":"{language}"}}
  ]
}}"""

    messages = [{"role": "user", "content": prompt}]
    content, used_model = smart_call(db, preferred, messages, max_tokens=8000, user_id=user_id)
    logger.info(f"write_article used model: {used_model}")

    try:
        return _extract_json(content)
    except TruncatedResponseError as e:
        logger.warning(f"write_article truncated ({len(e.partial_raw)} chars) — continuing with {used_model}")
        result = _continue_truncated_response(db, messages, e.partial_raw, preferred, user_id=user_id, max_tokens=4000, close_with="}")
        if result is not None:
            logger.info("write_article continuation succeeded")
            return result
        raise ValueError(f"write_article failed: truncated and continuation failed.\n{e}")


def rewrite_article(
    db: Session,
    title: str,
    keywords: list[str],
    language: str,
    backlinks: list[dict],
    model: Optional[str] = None,
    user_id: int = None,
) -> dict:
    """Viết lại bài chưa được index."""
    preferred = model or get_setting(db, "openrouter_model", DEFAULT_OR_MODEL, user_id=user_id)
    lang_name = LANGUAGE_NAMES.get(language, "English")
    kw_str = ", ".join(keywords)
    now = datetime.now()
    current_date_str = now.strftime("%B %Y")
    current_year = now.year

    backlink_str = ""
    if backlinks:
        backlink_str = "\n".join(
            f'- Anchor text: "{bl.get("anchor", "")}" -> URL: {bl.get("url", "")}'
            for bl in backlinks
        )

    prompt = f"""The article "{title}" targeting [{kw_str}] failed to get indexed by Google. You must rewrite it completely — different angle, stronger structure, more value.

TODAY'S DATE: {current_date_str}
IMPORTANT: All content must reflect {current_year} as the current year. Do NOT reference 2025 as current — use {current_year} data and context.

Language: {lang_name}
Target keywords: {kw_str}
{f'Include these backlinks naturally:{chr(10)}{backlink_str}' if backlink_str else ''}

Rewrite strategy — make it rank AND pass AI detection:
1. **New title** — completely different angle, more specific, power word or number included
2. **Stronger intro** — open with a relatable scenario or surprising fact, not a generic definition
3. **Deeper content** — 1200–1800 words; cover the topic more thoroughly than competing pages
4. **Better structure** — 4–5 H2 sections + FAQ (3 questions + answers) + conclusion with CTA
5. **E-E-A-T signals** — specific tips, real examples, first-person observations, expert tone
6. **Human writing style**:
   - Mix short sentences (3–6 words) with longer ones — vary sentence length constantly
   - NEVER use: "Furthermore", "Moreover", "In addition", "It is worth noting", "Delve into", "Leverage", "In conclusion"
   - Use natural connectors: "Here's the thing.", "And that's why...", "Honestly,", "The good news?"
   - Include 1–2 rhetorical questions and 1 first-person observation
7. **HTML only**: h2, h3, p, ul, ol, li, strong — no html/head/body tags

Return ONLY valid JSON (no markdown, no extra text):
{{
  "title": "New compelling title in {lang_name}",
  "content": "Full rewritten HTML article in {lang_name}"
}}"""

    rewrite_messages = [{"role": "user", "content": prompt}]
    content, used_model = smart_call(db, preferred, rewrite_messages, max_tokens=6000, user_id=user_id)
    logger.info(f"rewrite_article used model: {used_model}")

    try:
        return _extract_json(content)
    except TruncatedResponseError as e:
        logger.warning(f"rewrite_article truncated — continuing with {used_model}")
        result = _continue_truncated_response(db, rewrite_messages, e.partial_raw, preferred, user_id=user_id, max_tokens=3000, close_with="}")
        if result is not None:
            return result
        raise ValueError(f"rewrite_article failed: truncated and continuation failed.\n{e}")
