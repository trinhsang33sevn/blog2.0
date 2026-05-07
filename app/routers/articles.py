from threading import Thread

from fastapi import APIRouter, Depends, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import Article, BlogspotSite, Project
from ..services.scheduler import write_articles_now
from ..templates import templates

router = APIRouter()


def _reset_article_for_retry(article: Article) -> None:
    # Nếu đã có nội dung → chỉ cần đăng lại, không viết lại (tiết kiệm AI)
    article.status        = "ready" if article.content else "pending"
    article.retry_count   = 0
    article.error_message = None


def _int_or_none(val: str | None) -> int | None:
    try:
        return int(val) if val and val.strip() else None
    except (ValueError, TypeError):
        return None


@router.get("/articles", response_class=HTMLResponse)
def articles_page(
    request: Request,
    project_id: str = Query(None),
    site_id: str = Query(None),
    status: str = Query(None),
    page: str = Query("1"),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    project_id = _int_or_none(project_id)
    site_id    = _int_or_none(site_id)
    page       = max(1, _int_or_none(page) or 1)
    status     = status.strip() if status and status.strip() else None

    query = db.query(Article).join(Project).filter(Project.user_id == current_user.id)
    if project_id:
        query = query.filter(Article.project_id == project_id)
    if site_id:
        query = query.filter(Article.site_id == site_id)
    if status:
        query = query.filter(Article.status == status)

    total    = query.count()
    per_page = 20
    articles = query.order_by(Article.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    return templates.TemplateResponse(request, "articles.html", {
        "articles":         articles,
        "projects":         db.query(Project).filter(Project.user_id == current_user.id).all(),
        "sites":            db.query(BlogspotSite).filter(BlogspotSite.user_id == current_user.id).all(),
        "total":            total,
        "page":             page,
        "per_page":         per_page,
        "total_pages":      (total + per_page - 1) // per_page,
        "filter_project_id": project_id,
        "filter_site_id":   site_id,
        "filter_status":    status,
        "current_user":     current_user,
        "active_page":      "articles",
    })


@router.post("/articles/bulk-delete")
def bulk_delete_articles(
    request: Request,
    ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if ids:
        articles = (
            db.query(Article).join(Project)
            .filter(Article.id.in_(ids), Project.user_id == current_user.id)
            .all()
        )
        for a in articles:
            db.delete(a)
        db.commit()
        count = len(articles)
    else:
        count = 0
    return RedirectResponse(f"/articles?success=Da+xoa+{count}+bai+viet", status_code=303)


@router.post("/articles/bulk-retry")
def bulk_retry_articles(
    request: Request,
    ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    valid_ids = []
    if ids:
        articles = (
            db.query(Article).join(Project)
            .filter(Article.id.in_(ids), Project.user_id == current_user.id)
            .all()
        )
        for a in articles:
            _reset_article_for_retry(a)
            valid_ids.append(a.id)
        db.commit()
    if valid_ids:
        Thread(target=write_articles_now, args=(valid_ids,), daemon=True).start()
    count = len(valid_ids)
    return RedirectResponse(f"/articles?success=Dang+viet+lai+{count}+bai+viet", status_code=303)


@router.post("/articles/{article_id}/retry")
def retry_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    article = (
        db.query(Article).join(Project)
        .filter(Article.id == article_id, Project.user_id == current_user.id)
        .first()
    )
    if article and article.status in ("failed", "pending"):
        _reset_article_for_retry(article)
        db.commit()
        Thread(target=write_articles_now, args=([article.id],), daemon=True).start()
    return RedirectResponse("/articles?success=Dang+viet+lai+bai+viet", status_code=303)
