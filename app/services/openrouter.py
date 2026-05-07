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


def call_openrouter(api_key: str, model: str, messages: list, max_tokens: int = 4000) -> str:
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
    return call_with_fallback(db, or_key, or_model, messages, max_tokens)


def call_with_fallback(
    db: Session,
    api_key: str,
    preferred_model: str,
    messages: list,
    max_tokens: int = 4000,
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

    # Partition once — each model checked exactly once, preserving order
    full_order = [preferred_model] + all_fallback_ids
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
                content = call_openrouter(api_key, model_id, messages, max_tokens)
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


# ─── JSON Parsing ────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict | list:
    """
    Trích xuất JSON từ response AI, xử lý các trường hợp:
    - Có markdown code block ```json ... ```
    - Có ký tự điều khiển (literal newline/tab) bên trong chuỗi JSON
    - JSON bị bọc bởi text thừa
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
    # Thay literal control chars (0x00-0x1f trừ \t \n \r) bằng space
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text)
    # Thay literal newline/tab bên trong chuỗi JSON bằng escaped version
    cleaned = re.sub(r'(?<=: ")(.*?)(?="[,\n\}])', lambda m: m.group(0).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'), cleaned, flags=re.DOTALL)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Cannot parse AI response as JSON: {e}\nRaw (first 500 chars): {raw[:500]}")


# ─── Public AI Functions ──────────────────────────────────────────────────────

_CLUSTER_BATCH_SIZE = 60  # từ khóa tối ưu mỗi lô


def _cluster_batch(
    db: Session,
    keywords: list[str],
    preferred: str,
    user_id: int = None,
) -> list[dict]:
    """Gọi AI phân cụm cho một lô từ khóa."""
    kw_list = "\n".join(f"- {kw}" for kw in keywords)
    prompt = f"""You are a senior SEO strategist. Group the following keywords into topical clusters. Each cluster will become ONE comprehensive SEO article.

Keywords to cluster:
{kw_list}

Rules:
1. Group by SEARCH INTENT — all keywords in a cluster must share the same intent (informational, commercial, or transactional).
2. Each cluster = one article. Keep clusters focused: 2–6 keywords per cluster is ideal.
3. Cluster "name" must be a specific, article-ready topic title — not generic (e.g. "How to Train a German Shepherd Puppy at Home", not "Dog Training").
4. Do NOT split closely related long-tail variations into different clusters.
5. Do NOT merge unrelated keywords just to reduce count.
6. Every keyword must appear in exactly one cluster.

Return ONLY a valid JSON array — no explanation, no markdown, nothing else:
[
  {{
    "name": "Specific SEO Article Topic",
    "keywords": ["primary keyword", "variation 1", "long-tail variant"]
  }}
]"""

    content, used_model = smart_call(
        db, preferred,
        [{"role": "user", "content": prompt}],
        max_tokens=3000,
        user_id=user_id,
    )
    logger.info(f"cluster_batch ({len(keywords)} kws) used model: {used_model}")
    return _extract_json(content)


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
    user_id: int = None,
) -> dict:
    """Phân tích search intent rồi viết bài unique."""
    preferred = model or get_setting(db, "openrouter_model", DEFAULT_OR_MODEL, user_id=user_id)
    lang_name = LANGUAGE_NAMES.get(language, "English")
    kw_str = ", ".join(keywords)
    now = datetime.now()
    current_date_str = now.strftime("%B %Y")   # e.g. "May 2026"
    current_year = now.year

    backlink_str = ""
    if backlinks:
        lines = "\n".join(
            f'- Anchor text: "{bl.get("anchor", "")}" -> URL: {bl.get("url", "")}'
            for bl in backlinks
        )
        backlink_str = f"\nInclude these backlinks naturally in the article:\n{lines}"

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

    prompt = f"""{persona_intro}

TODAY'S DATE: {current_date_str}
IMPORTANT: All content must reflect {current_year} as the current year. Do NOT cite 2025 data as "current", "this year", or "recently" — that is outdated. Use {current_year} figures, trends, and context throughout.

## STEP 1 — Search Intent Analysis
Topic cluster: {cluster_name}
Keywords: {kw_str}
Identify:
- Primary intent: what the user REALLY wants (learn / compare / buy / solve a problem)
- Key pain points or questions behind these keywords
- What the ideal article must deliver to satisfy this intent fully
{existing_str}
{angle_section}
## STEP 2 — Write a Complete Article in {lang_name}
{backlink_str}
{internal_link_str}

### Article structure (MANDATORY):
1. **Title** — Specific, click-worthy. Include the primary keyword + a power element (number, "Guide", "Proven", "Step-by-Step"). Avoid generic titles.
2. **Introduction (hook)** — Start with a relatable scenario, surprising fact, or bold statement that makes the reader feel understood. 2–3 paragraphs. State what they'll gain.
3. **Body sections** — 4–6 H2 sections. Each covers one focused sub-topic. Use H3 for sub-points. Include at least one: numbered steps, comparison, checklist, or real example.
4. **FAQ section** — 3–4 genuine questions users ask, with concise H3 + answer. Helps capture featured snippets.
5. **Conclusion** — Brief takeaways + clear CTA.

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

## STEP 3 — Image Suggestions (in English)
- image_prompt: One VIVID, highly specific sentence for an AI image generator. Describe a real scene — specific subject, action, setting, mood, lighting. NOT generic clip-art concepts. Example: "A Vietnamese woman in her 30s sitting at a wooden desk reviewing financial documents on her laptop, warm morning light through a window, coffee cup beside her, charts visible on screen, photorealistic."
- image_queries: 3–5 short English keyword phrases (2–4 words each) for stock photo searches, one per main H2 section. Make them concrete and visual — avoid abstract terms.

## STEP 4 — Article Labels (in {lang_name})
Generate 1–3 concise blog category labels in {lang_name}.
Labels must be short broad topic words (e.g. "Sức khỏe", "Dinh dưỡng", "Thể thao").
Do NOT use the article title as a label. Max 30 characters each.

Return ONLY valid JSON — no markdown fences, no extra text, nothing before or after the JSON:
{{
  "intent_analysis": "1–2 sentence summary of search intent and what the article delivers (in English)",
  "title": "SEO-optimized article title in {lang_name}",
  "content": "Complete HTML article in {lang_name} (all sections including FAQ and conclusion)",
  "image_prompt": "Vivid, specific English scene description for AI image generation",
  "image_queries": ["concrete visual query 1", "concrete visual query 2", "concrete visual query 3", "concrete visual query 4"],
  "labels": ["Nhãn 1", "Nhãn 2"]
}}"""

    content, used_model = smart_call(db, preferred, [{"role": "user", "content": prompt}], max_tokens=5000, user_id=user_id)
    logger.info(f"write_article used model: {used_model}")
    return _extract_json(content)


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

    content, used_model = smart_call(db, preferred, [{"role": "user", "content": prompt}], max_tokens=5000, user_id=user_id)
    logger.info(f"rewrite_article used model: {used_model}")
    return _extract_json(content)
