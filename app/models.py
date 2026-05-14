from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Date, Float,
    ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship
from .database import Base


PLAN_LIMITS = {
    "free":     {"projects": 1,    "sites": 1,   "articles_per_day": 5},
    "pro":      {"projects": 5,    "sites": 10,  "articles_per_day": 35},
    "business": {"projects": None, "sites": None, "articles_per_day": None},
    "gift":     {"projects": 5,    "sites": 10,  "articles_per_day": 35},
}


class User(Base):
    __tablename__ = "users"
    id                  = Column(Integer, primary_key=True, index=True)
    email               = Column(String(200), unique=True, nullable=False, index=True)
    password_hash       = Column(String(500), nullable=False)
    full_name           = Column(String(200))
    is_active           = Column(Boolean, default=True)
    is_admin            = Column(Boolean, default=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    reset_token         = Column(String(200), nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)

    subscription      = relationship("Subscription", back_populates="user", uselist=False, cascade="all, delete-orphan")
    google_accounts   = relationship("GoogleAccount", back_populates="user")
    platform_accounts = relationship("PlatformAccount", back_populates="user")
    projects          = relationship("Project", back_populates="user", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id                    = Column(Integer, primary_key=True, index=True)
    user_id               = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    plan                  = Column(String(50), default="free")    # free / pro / business
    status                = Column(String(50), default="trial")   # trial / active / expired / cancelled
    started_at            = Column(DateTime, default=datetime.utcnow)
    expires_at            = Column(DateTime)                       # None = never expires
    projects_limit        = Column(Integer, default=1)
    sites_limit           = Column(Integer, default=1)
    articles_per_day_limit = Column(Integer, default=5)

    user = relationship("User", back_populates="subscription")

    @property
    def is_active_plan(self) -> bool:
        if self.expires_at is None:
            return True
        return datetime.utcnow() < self.expires_at


class Author(Base):
    __tablename__ = "authors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    bio = Column(Text)
    expertise = Column(Text, default="[]")       # JSON array of topic areas
    writing_style = Column(Text)
    tone = Column(String(100), default="conversational")
    success_score = Column(Float, default=1.0)   # updated by feedback loop
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContentAngle(Base):
    __tablename__ = "content_angles"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    angle_type = Column(String(100))
    success_score = Column(Float, default=1.0)
    is_active = Column(Boolean, default=True)


class AppSetting(Base):
    __tablename__ = "app_settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)


class GoogleAccount(Base):
    __tablename__ = "google_accounts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(200))
    email = Column(String(200), unique=True)
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expiry = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    user  = relationship("User", back_populates="google_accounts")
    sites = relationship("BlogspotSite", back_populates="account", cascade="all, delete-orphan")


class PlatformAccount(Base):
    """Non-Google platform credentials: WordPress.com, Tumblr, Hashnode."""
    __tablename__ = "platform_accounts"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    platform      = Column(String(50), nullable=False)   # wordpress | tumblr | hashnode
    name          = Column(String(200))                  # display name / username
    access_token  = Column(Text)                         # OAuth2 token or API key
    refresh_token = Column(Text)
    token_expiry  = Column(DateTime)
    created_at    = Column(DateTime, default=datetime.utcnow)

    user  = relationship("User", back_populates="platform_accounts")
    sites = relationship("BlogspotSite", back_populates="platform_account")


class BlogspotSite(Base):
    __tablename__ = "blogspot_sites"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("google_accounts.id"), nullable=True)
    platform_account_id = Column(Integer, ForeignKey("platform_accounts.id"), nullable=True)
    platform  = Column(String(50), default="blogspot")  # blogspot|wordpress|tumblr|hashnode
    blog_id   = Column(String(100))   # blogger blog ID / WP site ID / Tumblr blog name / Hashnode pub ID
    blog_url  = Column(String(500))
    blog_name = Column(String(300))
    category  = Column(String(200))
    default_language = Column(String(20), default="vi")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    account          = relationship("GoogleAccount", back_populates="sites")
    platform_account = relationship("PlatformAccount", back_populates="sites")
    project_sites    = relationship("ProjectSite", back_populates="site")


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(300), nullable=False)
    description = Column(Text)
    status = Column(String(50), default="pending")  # pending/running/paused/completed
    articles_per_day = Column(Integer, default=3)
    min_interval_minutes = Column(Integer, default=60)
    max_interval_minutes = Column(Integer, default=240)
    backlinks = Column(Text, default="[]")  # JSON array: [{"url": "...", "anchor": "..."}]
    custom_labels = Column(Text, default="[]")  # JSON array: ["Nhãn 1", "Nhãn 2"]
    ai_model = Column(String(200), default="meta-llama/llama-3.1-8b-instruct:free")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user              = relationship("User", back_populates="projects")
    keywords          = relationship("Keyword", back_populates="project", cascade="all, delete-orphan")
    clusters          = relationship("KeywordCluster", back_populates="project", cascade="all, delete-orphan")
    project_sites     = relationship("ProjectSite", back_populates="project", cascade="all, delete-orphan")
    articles          = relationship("Article", back_populates="project", cascade="all, delete-orphan")


class ProjectSite(Base):
    __tablename__ = "project_sites"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    site_id = Column(Integer, ForeignKey("blogspot_sites.id"), nullable=False)
    language = Column(String(20), default="vi")
    last_published_at = Column(DateTime)
    articles_today = Column(Integer, default=0)
    last_count_reset = Column(Date)

    __table_args__ = (UniqueConstraint("project_id", "site_id"),)

    project = relationship("Project", back_populates="project_sites")
    site = relationship("BlogspotSite", back_populates="project_sites")


class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    keyword = Column(String(500), nullable=False)
    status = Column(String(50), default="pending")  # pending/clustered
    cluster_id = Column(Integer, ForeignKey("keyword_clusters.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="keywords")
    cluster = relationship("KeywordCluster", back_populates="keywords")


class KeywordCluster(Base):
    __tablename__ = "keyword_clusters"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    cluster_name = Column(String(500))
    intent_analysis = Column(Text)  # AI analysis result
    status = Column(String(50), default="pending")  # pending/writing/completed
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="clusters")
    keywords = relationship("Keyword", back_populates="cluster")
    articles = relationship("Article", back_populates="cluster")


class Article(Base):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("keyword_clusters.id"), nullable=True)
    site_id = Column(Integer, ForeignKey("blogspot_sites.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    title = Column(String(500))
    content = Column(Text)
    url = Column(String(1000))
    blogger_post_id = Column(String(100))
    language = Column(String(20), default="vi")
    labels = Column(Text, default="[]")  # JSON array: nhãn AI tự sinh
    # pending -> writing -> ready -> published / failed
    status = Column(String(50), default="pending")
    error_message = Column(Text)
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    retry_count = Column(Integer, default=0)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=True)
    content_angle_id = Column(Integer, ForeignKey("content_angles.id"), nullable=True)

    cluster = relationship("KeywordCluster", back_populates="articles")
    site = relationship("BlogspotSite")
    project = relationship("Project", back_populates="articles")
    index_task = relationship("IndexTask", back_populates="article", uselist=False, cascade="all, delete-orphan")
    author = relationship("Author")
    content_angle = relationship("ContentAngle")


class IndexTask(Base):
    __tablename__ = "index_tasks"
    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, ForeignKey("articles.id"), unique=True, nullable=False)
    url = Column(String(1000), nullable=False)
    # pending -> submitted -> indexed / failed
    status = Column(String(50), default="pending")
    submitted_at = Column(DateTime)
    last_checked_at = Column(DateTime)
    next_check_at = Column(DateTime)
    check_count = Column(Integer, default=0)
    sinbyte_task_id = Column(String(200))
    sinbyte_submitted_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    article = relationship("Article", back_populates="index_task")
