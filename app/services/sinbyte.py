import httpx
from sqlalchemy.orm import Session
from .openrouter import get_setting

SINBYTE_API_BASE = "https://app.sinbyte.com/api/indexing"


def _get_api_key(db: Session, user_id: int = None) -> str:
    return get_setting(db, "sinbyte_api_key", user_id=user_id)


def submit_urls(db: Session, task_name: str, urls: list[str], user_id: int = None) -> dict:
    """Submit URLs to Sinbyte for indexing."""
    api_key = _get_api_key(db, user_id=user_id)
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


def get_task_detail(db: Session, task_id: str, user_id: int = None) -> dict:
    api_key = _get_api_key(db, user_id=user_id)
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{SINBYTE_API_BASE}/{task_id}/", params={"apikey": api_key})
        resp.raise_for_status()
        return resp.json()


def get_all_tasks(db: Session, user_id: int = None) -> list:
    api_key = _get_api_key(db, user_id=user_id)
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{SINBYTE_API_BASE}/", params={"apikey": api_key})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
