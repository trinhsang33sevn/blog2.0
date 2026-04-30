from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import init_db, get_db, SessionLocal
from .dependencies import get_current_user
from .models import Project, Article, IndexTask, BlogspotSite
from .services.scheduler import start_scheduler, stop_scheduler
from .services.agent_service import seed_agents
from .routers import accounts, projects, articles, indexing, settings_router
from .routers import auth as auth_router
from .routers import admin as admin_router
from .templates import templates

settings = get_settings()

_PUBLIC_PREFIXES = ("/login", "/register", "/static")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        if not request.session.get("user_id"):
            return RedirectResponse(url="/login")
        return await call_next(request)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed_agents(db)
    finally:
        db.close()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="AutoBlogspot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

app.include_router(auth_router.router)
app.include_router(accounts.router)
app.include_router(projects.router)
app.include_router(articles.router)
app.include_router(indexing.router)
app.include_router(settings_router.router)
app.include_router(admin_router.router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not current_user:
        return templates.TemplateResponse(request, "landing.html", {})

    q_sites    = db.query(BlogspotSite).filter(BlogspotSite.is_active == True, BlogspotSite.user_id == current_user.id)
    q_projects = db.query(Project).filter(Project.user_id == current_user.id)
    q_articles = db.query(Article).join(Project).filter(Project.user_id == current_user.id)
    q_index    = db.query(IndexTask).join(Article).join(Project).filter(Project.user_id == current_user.id)

    recent_articles = (
        q_articles.filter(Article.status == "published")
        .order_by(Article.published_at.desc())
        .limit(8).all()
    )

    return templates.TemplateResponse(request, "dashboard.html", {
        "total_sites":           q_sites.count(),
        "total_projects":        q_projects.count(),
        "running_projects":      q_projects.filter(Project.status == "running").count(),
        "total_articles":        q_articles.count(),
        "published_articles":    q_articles.filter(Article.status == "published").count(),
        "indexed_articles":      q_index.filter(IndexTask.status == "indexed").count(),
        "pending_index":         q_index.filter(IndexTask.status.in_(["pending", "submitted"])).count(),
        "recent_articles":       recent_articles,
        "running_projects_list": q_projects.filter(Project.status == "running").all(),
        "current_user":          current_user,
        "active_page":           "dashboard",
    })
