import re

_PLAN_CODES   = {"pro": "PRO", "business": "BIZ"}
_PLAN_AMOUNTS = {"pro": 200_000, "business": 500_000}
MONTH_OPTIONS = [1, 3, 6, 12]


def generate_reference(user_id: int, plan: str, months: int = 1) -> str:
    """Tạo mã chuyển khoản: ABLOG{user_id}{PLAN_CODE}{months:02d}"""
    code = _PLAN_CODES.get(plan, plan.upper())
    return f"ABLOG{user_id}{code}{months:02d}"


def parse_reference(content: str) -> tuple[int, str, int] | None:
    """Trích xuất (user_id, plan, months) từ nội dung chuyển khoản.
    Hỗ trợ format mới (ABLOG123PRO03) và cũ (ABLOG123PRO) để tương thích ngược."""
    upper = content.upper()
    # Format mới có số tháng ở cuối
    m = re.search(r"ABLOG(\d+)(PRO|BIZ)(\d{1,2})", upper)
    if m:
        user_id = int(m.group(1))
        plan    = "pro" if m.group(2) == "PRO" else "business"
        months  = max(1, int(m.group(3)))
        return user_id, plan, months
    # Format cũ không có tháng → mặc định 1 tháng
    m = re.search(r"ABLOG(\d+)(PRO|BIZ)", upper)
    if m:
        user_id = int(m.group(1))
        plan    = "pro" if m.group(2) == "PRO" else "business"
        return user_id, plan, 1
    return None


def expected_amount(plan: str, months: int = 1) -> int:
    return _PLAN_AMOUNTS.get(plan, 0) * months


def build_qr_url(account_number: str, bank_name: str, amount: int, reference: str) -> str:
    """Tạo URL ảnh VietQR từ SePay."""
    from urllib.parse import urlencode
    params = urlencode({
        "acc":      account_number,
        "bank":     bank_name,
        "amount":   amount,
        "des":      reference,
        "template": "compact",
    })
    return f"https://qr.sepay.vn/img?{params}"
