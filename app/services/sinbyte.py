import httpx
from sqlalchemy.orm import Session
from ..models import AppSetting

SINBYTE_API_BASE = "https://app.sinbyte.com/api/indexing"


def get_api_key(db: Session) -> str:
    row = db.query(AppSetting).filter(AppSetting.key == "sinbyte_api_key").first()
    return row.value if row else ""


def submit_urls(db: Session, task_name: str, urls: list[str]) -> dict:
    """Submit URLs to Sinbyte for indexing."""
    api_key = get_api_key(db)
    if not api_key:
        raise ValueError("Sinbyte API key chưa được cấu hình")

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{SINBYTE_API_BASE}/",
            json={
                "apikey": api_key,
                "name": task_name,
                "urls": urls,
            },
        )
        resp.raise_for_status()
        return resp.json()


def get_task_detail(db: Session, task_id: str) -> dict:
    api_key = get_api_key(db)
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{SINBYTE_API_BASE}/{task_id}/", params={"apikey": api_key})
        resp.raise_for_status()
        return resp.json()


def get_all_tasks(db: Session) -> list:
    api_key = get_api_key(db)
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{SINBYTE_API_BASE}/", params={"apikey": api_key})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
