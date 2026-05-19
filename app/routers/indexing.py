import csv
import io
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime

from ..database import get_db
from ..dependencies import get_current_user
from ..models import IndexTask, Article, Project, BlogspotSite
from ..services import sinbyte
from ..templates import templates

router = APIRouter()


@router.get("/indexing", response_class=HTMLResponse)
def indexing_page(
    request: Request,
    status: str = Query(None),
    project_id: str = Query(None),
    site_id: str = Query(None),
    page: str = Query("1"),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    try:
        page = max(1, int(page)) if page and page.strip() else 1
    except (ValueError, TypeError):
        page = 1

    status     = status.strip()     if status     and status.strip()     else None
    project_id = project_id.strip() if project_id and project_id.strip() else None
    site_id    = site_id.strip()    if site_id    and site_id.strip()    else None

    base = (
        db.query(IndexTask)
        .join(Article, IndexTask.article_id == Article.id)
        .join(Project, Article.project_id == Project.id)
        .filter(Project.user_id == current_user.id)
    )
    if status:
        base = base.filter(IndexTask.status == status)
    if project_id:
        base = base.filter(Article.project_id == int(project_id))
    if site_id:
        base = base.filter(Article.site_id == int(site_id))

    total    = base.count()
    per_page = 20
    tasks    = base.order_by(IndexTask.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    counts = {}
    for s in ("pending", "submitted", "indexed", "skipped", "failed"):
        counts[s] = (
            db.query(IndexTask).join(Article).join(Project)
            .filter(Project.user_id == current_user.id, IndexTask.status == s)
            .count()
        )
    counts["all"] = sum(counts.values())

    # Lists for filter dropdowns
    projects = (
        db.query(Project)
        .filter(Project.user_id == current_user.id)
        .order_by(Project.name)
        .all()
    )
    sites = (
        db.query(BlogspotSite)
        .filter(BlogspotSite.user_id == current_user.id)
        .order_by(BlogspotSite.blog_name)
        .all()
    )

    return templates.TemplateResponse(request, "indexing.html", {
        "tasks": tasks, "counts": counts, "total": total,
        "page": page, "per_page": per_page,
        "total_pages":      (total + per_page - 1) // per_page,
        "filter_status":    status,
        "filter_project_id": int(project_id) if project_id else None,
        "filter_site_id":    int(site_id)    if site_id    else None,
        "projects": projects, "sites": sites,
        "current_user": current_user,
        "active_page":  "indexing",
        "now":          datetime.utcnow(),
    })


@router.get("/indexing/export")
def export_indexing_csv(
    request: Request,
    status: str = Query(None),
    project_id: str = Query(None),
    site_id: str = Query(None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)

    status     = status.strip()     if status     and status.strip()     else None
    project_id = project_id.strip() if project_id and project_id.strip() else None
    site_id    = site_id.strip()    if site_id    and site_id.strip()    else None

    base = (
        db.query(IndexTask)
        .join(Article, IndexTask.article_id == Article.id)
        .join(Project, Article.project_id == Project.id)
        .filter(Project.user_id == current_user.id)
    )
    if status:
        base = base.filter(IndexTask.status == status)
    if project_id:
        base = base.filter(Article.project_id == int(project_id))
    if site_id:
        base = base.filter(Article.site_id == int(site_id))

    tasks = base.order_by(IndexTask.created_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "URL", "Tiêu đề bài viết", "Website", "Dự án", "Trạng thái",
                     "Gửi Sinbyte", "Lần gửi", "Kiểm tra cuối", "Kiểm tra tiếp", "Tạo lúc"])
    for t in tasks:
        article    = t.article
        site_name  = article.site.blog_name  if article and article.site    else ""
        proj_name  = article.project.name    if article and article.project else ""
        title      = article.title           if article else ""
        submitted  = t.submitted_at.strftime("%Y-%m-%d %H:%M")    if t.submitted_at    else ""
        last_check = t.last_checked_at.strftime("%Y-%m-%d %H:%M") if t.last_checked_at else ""
        next_check = t.next_check_at.strftime("%Y-%m-%d %H:%M")   if t.next_check_at   else ""
        created    = t.created_at.strftime("%Y-%m-%d %H:%M")       if t.created_at      else ""
        writer.writerow([t.id, t.url, title, site_name, proj_name, t.status,
                         submitted, t.sinbyte_submitted_count or 0, last_check, next_check, created])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=indexing.csv"},
    )


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
