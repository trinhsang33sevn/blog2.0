from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime

from ..database import get_db
from ..dependencies import get_current_user
from ..models import IndexTask, Article, Project
from ..services import sinbyte
from ..templates import templates

router = APIRouter()


@router.get("/indexing", response_class=HTMLResponse)
def indexing_page(
    request: Request,
    status: str = Query(None),
    page: str = Query("1"),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    try:
        page = max(1, int(page)) if page and page.strip() else 1
    except (ValueError, TypeError):
        page = 1
    status = status.strip() if status and status.strip() else None

    base = db.query(IndexTask).join(Article).join(Project).filter(Project.user_id == current_user.id)
    if status:
        base = base.filter(IndexTask.status == status)

    total    = base.count()
    per_page = 20
    tasks    = base.order_by(IndexTask.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    counts = {}
    for s in ("pending", "submitted", "indexed", "failed"):
        counts[s] = (
            db.query(IndexTask).join(Article).join(Project)
            .filter(Project.user_id == current_user.id, IndexTask.status == s)
            .count()
        )
    counts["all"] = sum(counts.values())

    return templates.TemplateResponse(request, "indexing.html", {
        "tasks": tasks, "counts": counts, "total": total,
        "page": page, "per_page": per_page,
        "total_pages":   (total + per_page - 1) // per_page,
        "filter_status": status, "current_user": current_user,
        "active_page":   "indexing", "now": datetime.utcnow(),
    })


@router.post("/indexing/{task_id}/resubmit")
def resubmit_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    task = (
        db.query(IndexTask).join(Article).join(Project)
        .filter(IndexTask.id == task_id, Project.user_id == current_user.id)
        .first()
    )
    if task:
        try:
            result = sinbyte.submit_urls(db, f"Manual-{datetime.utcnow().strftime('%Y%m%d')}", [task.url], user_id=current_user.id)
            task.sinbyte_task_id         = str(result.get("id", ""))
            task.sinbyte_submitted_count += 1
            task.submitted_at            = datetime.utcnow()
            task.status                  = "submitted"
            db.commit()
            return RedirectResponse("/indexing?success=URL+resubmitted+to+Sinbyte", status_code=303)
        except Exception as e:
            return RedirectResponse(f"/indexing?error={str(e)}", status_code=303)
    return RedirectResponse("/indexing", status_code=303)
