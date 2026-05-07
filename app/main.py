import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import init_db, get_db, SessionLocal
from .dependencies import get_current_user
from .i18n import set_lang, SUPPORTED_LANGUAGES
from .models import Project, Article, IndexTask, BlogspotSite
from .services.scheduler import start_scheduler, stop_scheduler
from .services.agent_service import seed_agents
from .routers import accounts, projects, articles, indexing, settings_router
from .routers import auth as auth_router
from .routers import admin as admin_router
from .routers import blog as blog_router
from .templates import templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("autoblogspot")

settings = get_settings()

_PUBLIC_PREFIXES = ("/login", "/register", "/forgot-password", "/reset-password", "/static", "/set-lang", "/billing/webhook", "/robots.txt", "/sitemap.xml", "/health", "/blog", "/terms", "/privacy")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not settings.DEBUG:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# Simple sliding-window rate limiter (in-memory, per-IP)
_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_RULES: dict[str, tuple[int, int]] = {
    "/login":    (10, 60),   # 10 requests per 60s
    "/register": (5,  60),   # 5 requests per 60s
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            rule = _RATE_RULES.get(request.url.path)
            if rule:
                max_req, window = rule
                ip = request.client.host if request.client else "unknown"
                key = f"{ip}:{request.url.path}"
                now = time.time()
                hits = [t for t in _rate_store[key] if now - t < window]
                if len(hits) >= max_req:
                    logger.warning("Rate limit hit: %s on %s", ip, request.url.path)
                    return HTMLResponse(
                        "<h3>Too many requests. Please wait a moment.</h3>",
                        status_code=429,
                    )
                hits.append(now)
                _rate_store[key] = hits
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        if not request.session.get("user_id"):
            return RedirectResponse(url="/login")
        return await call_next(request)


class LangMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        lang = (request.query_params.get("lang")
                or request.cookies.get("lang")
                or request.session.get("lang", "vi"))
        set_lang(lang if lang in SUPPORTED_LANGUAGES else "vi")
        return await call_next(request)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_production()
    import os; os.makedirs("logs", exist_ok=True)
    init_db()
    db = SessionLocal()
    try:
        seed_agents(db)
    finally:
        db.close()
    start_scheduler()
    logger.info("AutoBlogspot started")
    yield
    stop_scheduler()
    logger.info("AutoBlogspot stopped")


app = FastAPI(title="AutoBlogspot", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(LangMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="abs_session",
    https_only=not settings.DEBUG,
    same_site="strict",
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.warning("HTTP %s on %s", exc.status_code, request.url.path)
    if exc.status_code == 404:
        return HTMLResponse(
            content=templates.get_template("404.html").render({}),
            status_code=404,
        )
    if exc.status_code == 403:
        return RedirectResponse(url="/login")
    return HTMLResponse(
        content=templates.get_template("500.html").render({"detail": str(exc.detail)}),
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s", request.url.path)
    detail = str(exc) if settings.DEBUG else None
    return HTMLResponse(
        content=templates.get_template("500.html").render({"detail": detail}),
        status_code=500,
    )

app.include_router(auth_router.router)
app.include_router(accounts.router)
app.include_router(projects.router)
app.include_router(articles.router)
app.include_router(indexing.router)
app.include_router(settings_router.router)
app.include_router(admin_router.router)
app.include_router(blog_router.router)


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


@app.get("/health")
def health_check():
    from .database import SessionLocal as _SL
    try:
        db = _SL()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        db_ok = False
    status = "ok" if db_ok else "degraded"
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": status, "db": db_ok, "version": "1.0.0"})


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /dashboard\n"
        "Disallow: /projects\n"
        "Disallow: /articles\n"
        "Disallow: /accounts\n"
        "Disallow: /billing\n"
        "Disallow: /settings\n"
        "Disallow: /indexing\n"
        "Sitemap: https://autoblogspot.com/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
def sitemap_xml():
    from .blog_data import ARTICLES
    base = "https://autoblogspot.com"
    urls = [
        f'  <url><loc>{base}/</loc><priority>1.0</priority></url>',
        f'  <url><loc>{base}/login</loc><priority>0.5</priority></url>',
        f'  <url><loc>{base}/register</loc><priority>0.8</priority></url>',
        f'  <url><loc>{base}/blog</loc><priority>0.9</priority></url>',
    ] + [
        f'  <url><loc>{base}/blog/{a["slug"]}</loc><lastmod>{a["date"]}</lastmod><priority>0.8</priority></url>'
        for a in ARTICLES
    ]
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n"
        '</urlset>\n'
    )
    return Response(content=content, media_type="application/xml")


@app.get("/set-lang")
def set_language(request: Request, lang: str = "vi", next: str = "/"):
    lang = lang if lang in SUPPORTED_LANGUAGES else "vi"
    request.session["lang"] = lang
    response = RedirectResponse(url=next)
    response.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365)
    return response
