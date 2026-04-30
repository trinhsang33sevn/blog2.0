import json
from threading import Thread

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, can_create_project
from ..models import Project, ProjectSite, BlogspotSite, Keyword, KeywordCluster, Article
from ..services.openrouter import FREE_MODELS, LANGUAGE_NAMES, get_setting
from ..services.ai_providers import CLAUDE_MODELS, OPENAI_MODELS, GROQ_MODELS, GEMINI_MODELS
from ..services.scheduler import process_keyword_clustering


def _get_available_models(db, user_id: int) -> dict:
    """Return model groups based on which API keys the user has configured."""
    groups = {"openrouter": FREE_MODELS, "gemini": [], "claude": [], "openai": [], "groq": []}
    gemini_keys = get_setting(db, "gemini_api_keys", user_id=user_id)
    if gemini_keys and gemini_keys.strip():
        groups["gemini"] = GEMINI_MODELS
    if get_setting(db, "claude_api_key", user_id=user_id):
        groups["claude"] = CLAUDE_MODELS
    if get_setting(db, "openai_api_key", user_id=user_id):
        groups["openai"] = OPENAI_MODELS
    if get_setting(db, "groq_api_key", user_id=user_id):
        groups["groq"] = GROQ_MODELS
    return groups


def _get_default_model(groups: dict, current_model: str = None) -> str:
    """Trả về model mặc định theo ưu tiên: Gemini → Claude → OpenAI → Groq → OpenRouter."""
    if current_model:
        return current_model  # giữ nguyên nếu đã chọn
    for provider in ("gemini", "claude", "openai", "groq"):
        if groups.get(provider):
            return groups[provider][0]["id"]
    or_models = groups.get("openrouter", FREE_MODELS)
    return or_models[1]["id"] if len(or_models) > 1 else "meta-llama/llama-3.3-70b-instruct:free"
from ..templates import templates

router = APIRouter()
LANGUAGES = list(LANGUAGE_NAMES.items())


def _parse_project_form(backlinks_json: str, custom_labels_text: str) -> tuple[list, list]:
    try:
        backlinks = json.loads(backlinks_json) if backlinks_json else []
    except json.JSONDecodeError:
        backlinks = []
    custom_labels = [l.strip() for l in custom_labels_text.split(",") if l.strip()] if custom_labels_text.strip() else []
    return backlinks, custom_labels


def _cap_articles_per_day(value: int, user) -> int:
    sub = user.subscription
    if not sub or sub.articles_per_day_limit is None:
        return value
    return min(value, sub.articles_per_day_limit)


@router.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    projects = db.query(Project).filter(Project.user_id == current_user.id).order_by(Project.created_at.desc()).all()
    return templates.TemplateResponse(request, "projects.html", {
        "projects": projects, "current_user": current_user, "active_page": "projects",
    })


@router.get("/projects/new", response_class=HTMLResponse)
def new_project_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    sites = db.query(BlogspotSite).filter(
        BlogspotSite.user_id == current_user.id, BlogspotSite.is_active == True
    ).all()
    model_groups = _get_available_models(db, current_user.id)
    return templates.TemplateResponse(request, "project_form.html", {
        "sites": sites, "languages": LANGUAGES,
        "models": FREE_MODELS,
        "model_groups": model_groups,
        "default_model": _get_default_model(model_groups),
        "project": None, "current_user": current_user, "active_page": "projects",
    })


@router.post("/projects/new")
def create_project(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    articles_per_day: int = Form(3),
    min_interval: int = Form(60),
    max_interval: int = Form(240),
    ai_model: str = Form("meta-llama/llama-3.1-8b-instruct:free"),
    site_ids: list[int] = Form(default=[]),
    site_languages: list[str] = Form(default=[]),
    backlinks_json: str = Form("[]"),
    custom_labels_text: str = Form(""),
    keywords_text: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    ok, msg = can_create_project(current_user, db)
    if not ok:
        return RedirectResponse(f"/projects?error={msg}", status_code=303)

    backlinks, custom_labels = _parse_project_form(backlinks_json, custom_labels_text)
    project = Project(
        user_id=current_user.id,
        name=name, description=description,
        articles_per_day=_cap_articles_per_day(articles_per_day, current_user),
        min_interval_minutes=min_interval, max_interval_minutes=max_interval,
        ai_model=ai_model,
        backlinks=json.dumps(backlinks),
        custom_labels=json.dumps(custom_labels, ensure_ascii=False),
        status="pending",
    )
    db.add(project)
    db.flush()

    for i, site_id in enumerate(site_ids):
        lang = site_languages[i] if i < len(site_languages) else "vi"
        db.add(ProjectSite(project_id=project.id, site_id=site_id, language=lang))

    if keywords_text.strip():
        for kw in [k.strip() for k in keywords_text.splitlines() if k.strip()]:
            db.add(Keyword(project_id=project.id, keyword=kw))

    db.commit()
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(project_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    clusters = db.query(KeywordCluster).filter(KeywordCluster.project_id == project_id).all()
    keywords = db.query(Keyword).filter(Keyword.project_id == project_id).all()

    rows = db.query(Article.status, func.count().label("n")).filter(
        Article.project_id == project_id
    ).group_by(Article.status).all()
    counts = {status: n for status, n in rows}

    recent_articles = (
        db.query(Article)
        .filter(Article.project_id == project_id, Article.status == "published")
        .order_by(Article.published_at.desc()).limit(10).all()
    )

    return templates.TemplateResponse(request, "project_detail.html", {
        "project": project, "clusters": clusters, "keywords": keywords,
        "total_articles":  sum(counts.values()),
        "published":       counts.get("published", 0),
        "pending_count":   counts.get("pending", 0),
        "writing_count":   counts.get("writing", 0),
        "failed_count":    counts.get("failed", 0),
        "recent_articles": recent_articles,
        "backlinks":       json.loads(project.backlinks or "[]"),
        "current_user":    current_user,
        "active_page":     "projects",
    })


def _trigger_pipeline():
    process_keyword_clustering()


@router.post("/projects/{project_id}/start")
def start_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if not project:
        raise HTTPException(status_code=404)
    # Check subscription still active
    sub = current_user.subscription
    if not sub or not sub.is_active_plan:
        return RedirectResponse(f"/projects/{project_id}?error=Goi+dich+vu+het+han.+Vui+long+gia+han.", status_code=303)
    project.status = "running"
    db.commit()
    Thread(target=_trigger_pipeline, daemon=True).start()
    return RedirectResponse(f"/projects/{project_id}?success=Project+started", status_code=303)


@router.post("/projects/{project_id}/pause")
def pause_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if project:
        project.status = "paused"
        db.commit()
    return RedirectResponse(f"/projects/{project_id}?success=Project+paused", status_code=303)


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if not project:
        raise HTTPException(status_code=404)
    sub = current_user.subscription
    if not sub or not sub.is_active_plan:
        return RedirectResponse(f"/projects/{project_id}?error=Goi+dich+vu+het+han.+Vui+long+gia+han.", status_code=303)
    project.status = "running"
    db.commit()
    Thread(target=_trigger_pipeline, daemon=True).start()
    return RedirectResponse(f"/projects/{project_id}?success=Project+resumed", status_code=303)


@router.post("/projects/{project_id}/add-keywords")
def add_keywords(
    project_id: int, request: Request,
    keywords_text: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if not project:
        raise HTTPException(status_code=404)

    added = 0
    for kw_text in [k.strip() for k in keywords_text.splitlines() if k.strip()]:
        if not db.query(Keyword).filter(
            Keyword.project_id == project_id, Keyword.keyword == kw_text
        ).first():
            db.add(Keyword(project_id=project_id, keyword=kw_text))
            added += 1
    db.commit()
    return RedirectResponse(f"/projects/{project_id}?success=Added+{added}+keywords", status_code=303)


@router.get("/projects/{project_id}/edit", response_class=HTMLResponse)
def edit_project_page(project_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if not project:
        raise HTTPException(status_code=404)
    sites = db.query(BlogspotSite).filter(
        BlogspotSite.user_id == current_user.id, BlogspotSite.is_active == True
    ).all()
    selected_site_ids = {ps.site_id for ps in project.project_sites}
    site_languages    = {ps.site_id: ps.language for ps in project.project_sites}
    try:
        current_labels = ", ".join(json.loads(project.custom_labels or "[]"))
    except Exception:
        current_labels = ""
    model_groups = _get_available_models(db, current_user.id)
    return templates.TemplateResponse(request, "project_form.html", {
        "sites": sites, "languages": LANGUAGES,
        "models": FREE_MODELS,
        "model_groups": model_groups,
        "default_model": _get_default_model(model_groups, project.ai_model),
        "project": project, "selected_site_ids": selected_site_ids,
        "site_languages": site_languages, "current_labels": current_labels,
        "current_user": current_user, "active_page": "projects",
    })


@router.post("/projects/{project_id}/edit")
def edit_project(
    project_id: int, request: Request,
    name: str = Form(...), description: str = Form(""),
    articles_per_day: int = Form(3), min_interval: int = Form(60), max_interval: int = Form(240),
    ai_model: str = Form("meta-llama/llama-3.1-8b-instruct:free"),
    site_ids: list[int] = Form(default=[]),
    site_languages: list[str] = Form(default=[]),
    backlinks_json: str = Form("[]"), custom_labels_text: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if not project:
        raise HTTPException(status_code=404)

    backlinks, custom_labels = _parse_project_form(backlinks_json, custom_labels_text)
    project.name                = name
    project.description         = description
    project.articles_per_day    = _cap_articles_per_day(articles_per_day, current_user)
    project.min_interval_minutes = min_interval
    project.max_interval_minutes = max_interval
    project.ai_model            = ai_model
    project.backlinks           = json.dumps(backlinks)
    project.custom_labels       = json.dumps(custom_labels, ensure_ascii=False)

    existing     = {ps.site_id: ps for ps in project.project_sites}
    new_site_ids = set(site_ids)
    for site_id, ps in list(existing.items()):
        if site_id not in new_site_ids:
            db.delete(ps)
    for i, site_id in enumerate(site_ids):
        lang = site_languages[i] if i < len(site_languages) else "vi"
        if site_id in existing:
            existing[site_id].language = lang
        else:
            db.add(ProjectSite(project_id=project_id, site_id=site_id, language=lang))

    db.commit()
    return RedirectResponse(f"/projects/{project_id}?success=Da+cap+nhat+du+an", status_code=303)


@router.post("/projects/{project_id}/delete")
def delete_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == current_user.id
    ).first()
    if project:
        db.delete(project)
        db.commit()
    return RedirectResponse("/projects?success=Project+deleted", status_code=303)


@router.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    from ..models import PLAN_LIMITS
    from ..services.openrouter import get_setting
    payment_config = {
        "bank_name":      get_setting(db, "payment_bank_name") or "",
        "account_number": get_setting(db, "payment_account_number") or "",
        "account_holder": get_setting(db, "payment_account_holder") or "",
        "transfer_note":  get_setting(db, "payment_transfer_note") or "",
    }
    return templates.TemplateResponse(request, "billing.html", {
        "current_user":   current_user,
        "sub":            current_user.subscription,
        "plan_limits":    PLAN_LIMITS,
        "active_page":    "billing",
        "payment_config": payment_config,
    })
