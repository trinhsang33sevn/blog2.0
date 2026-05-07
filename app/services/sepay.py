import re

_PLAN_CODES = {"pro": "PRO", "business": "BIZ"}
_PLAN_AMOUNTS = {"pro": 200_000, "business": 500_000}


def generate_reference(user_id: int, plan: str) -> str:
    """Tạo mã chuyển khoản duy nhất cho từng user + plan."""
    code = _PLAN_CODES.get(plan, plan.upper())
    return f"ABLOG{user_id}{code}"


def parse_reference(content: str) -> tuple[int, str] | None:
    """Trích xuất (user_id, plan) từ nội dung chuyển khoản.
    Trả về None nếu không tìm thấy mã hợp lệ."""
    m = re.search(r"ABLOG(\d+)(PRO|BIZ)", content.upper())
    if not m:
        return None
    user_id = int(m.group(1))
    plan = "pro" if m.group(2) == "PRO" else "business"
    return user_id, plan


def expected_amount(plan: str) -> int:
    return _PLAN_AMOUNTS.get(plan, 0)


def build_qr_url(account_number: str, bank_name: str, amount: int, reference: str) -> str:
    """Tạo URL ảnh VietQR từ SePay."""
    from urllib.parse import urlencode, quote
    params = urlencode({
        "acc":      account_number,
        "bank":     bank_name,
        "amount":   amount,
        "des":      reference,
        "template": "compact",
    })
    return f"https://qr.sepay.vn/img?{params}"
