from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import get_settings

settings = get_settings()

_is_postgres = settings.DATABASE_URL.startswith("postgresql")

if _is_postgres:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
else:
    # SQLite — local dev only
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_user_subscriptions():
    """Create free-trial Subscription for every user that doesn't have one."""
    from datetime import datetime, timedelta
    from . import models as m

    db = SessionLocal()
    try:
        users_without_sub = (
            db.query(m.User)
            .outerjoin(m.Subscription, m.User.id == m.Subscription.user_id)
            .filter(m.Subscription.id.is_(None))
            .all()
        )
        if not users_without_sub:
            return
        limits = m.PLAN_LIMITS["free"]
        for user in users_without_sub:
            sub = m.Subscription(
                user_id=user.id,
                plan="free",
                status="trial",
                started_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(days=30),
                projects_limit=limits["projects"],
                sites_limit=limits["sites"],
                articles_per_day_limit=limits["articles_per_day"],
            )
            db.add(sub)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _migrate_system_oauth_to_admin():
    """
    Copy system-wide OAuth settings (no u{id}_ prefix) to admin user's
    per-user settings so old configs are not lost after the scoping change.
    """
    from . import models as m

    OAUTH_KEYS = [
        "google_client_id", "google_client_secret",
        "wp_client_id", "wp_client_secret",
        "tumblr_consumer_key", "tumblr_consumer_secret",
    ]
    db = SessionLocal()
    try:
        admin = db.query(m.User).filter(m.User.is_admin == True).order_by(m.User.id).first()
        if not admin:
            return

        for key in OAUTH_KEYS:
            sys_row = db.query(m.AppSetting).filter(m.AppSetting.key == key).first()
            if not sys_row or not sys_row.value:
                continue

            user_key = f"u{admin.id}_{key}"
            user_row = db.query(m.AppSetting).filter(m.AppSetting.key == user_key).first()
            if not user_row:
                db.add(m.AppSetting(key=user_key, value=sys_row.value))

        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _fix_blogspot_sites_nullable():
    """
    Recreate blogspot_sites if account_id is NOT NULL from an old schema.
    SQLite does not support ALTER COLUMN — table recreate required.
    Only runs on SQLite.
    """
    if _is_postgres:
        return

    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(blogspot_sites)")).fetchall()
        acct_col = next((c for c in cols if c[1] == "account_id"), None)
        if not acct_col or acct_col[3] == 0:
            return

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS _bss_new (
                id                  INTEGER PRIMARY KEY,
                user_id             INTEGER REFERENCES users(id),
                account_id          INTEGER REFERENCES google_accounts(id),
                platform_account_id INTEGER REFERENCES platform_accounts(id),
                platform            TEXT DEFAULT 'blogspot',
                blog_id             TEXT,
                blog_url            TEXT,
                blog_name           TEXT,
                category            TEXT,
                default_language    TEXT DEFAULT 'vi',
                is_active           BOOLEAN DEFAULT 1,
                created_at          DATETIME
            )
        """))
        conn.execute(text("""
            INSERT INTO _bss_new
                (id, user_id, account_id, platform_account_id, platform,
                 blog_id, blog_url, blog_name, category, default_language,
                 is_active, created_at)
            SELECT
                id, user_id, account_id, platform_account_id, platform,
                blog_id, blog_url, blog_name, category, default_language,
                is_active, created_at
            FROM blogspot_sites
        """))
        conn.execute(text("DROP TABLE blogspot_sites"))
        conn.execute(text("ALTER TABLE _bss_new RENAME TO blogspot_sites"))
        conn.commit()


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations():
    """Add new columns without data loss. Supports both SQLite and PostgreSQL."""
    if _is_postgres:
        _run_migrations_pg()
    else:
        _run_migrations_sqlite()
    _ensure_user_subscriptions()
    _migrate_system_oauth_to_admin()


def _run_migrations_pg():
    new_columns = [
        ("projects",       "custom_labels",       "TEXT DEFAULT '[]'"),
        ("projects",       "user_id",             "INTEGER REFERENCES users(id)"),
        ("projects",       "ai_model",            "TEXT DEFAULT 'meta-llama/llama-3.1-8b-instruct:free'"),
        ("articles",       "labels",              "TEXT DEFAULT '[]'"),
        ("articles",       "author_id",           "INTEGER REFERENCES authors(id)"),
        ("articles",       "content_angle_id",    "INTEGER REFERENCES content_angles(id)"),
        ("articles",       "retry_count",         "INTEGER DEFAULT 0"),
        ("articles",       "error_message",       "TEXT"),
        ("articles",       "published_at",        "TIMESTAMP"),
        ("articles",       "url",                 "TEXT"),
        ("articles",       "blogger_post_id",     "TEXT"),
        ("articles",       "language",            "TEXT DEFAULT 'vi'"),
        ("blogspot_sites", "user_id",             "INTEGER REFERENCES users(id)"),
        ("blogspot_sites", "default_language",    "TEXT DEFAULT 'vi'"),
        ("blogspot_sites", "is_active",           "BOOLEAN DEFAULT TRUE"),
        ("blogspot_sites", "platform",            "TEXT DEFAULT 'blogspot'"),
        ("blogspot_sites", "platform_account_id", "INTEGER REFERENCES platform_accounts(id)"),
        ("blogspot_sites", "category",            "TEXT"),
        ("google_accounts", "user_id",            "INTEGER REFERENCES users(id)"),
        ("project_sites",  "language",            "TEXT DEFAULT 'vi'"),
        ("project_sites",  "articles_today",      "INTEGER DEFAULT 0"),
        ("project_sites",  "last_count_reset",    "DATE"),
        ("project_sites",  "last_published_at",   "TIMESTAMP"),
        ("project_sites",  "next_publish_at",    "TIMESTAMP"),
        ("subscriptions",  "projects_limit",          "INTEGER DEFAULT 1"),
        ("subscriptions",  "sites_limit",             "INTEGER DEFAULT 1"),
        ("subscriptions",  "articles_per_day_limit",  "INTEGER DEFAULT 5"),
        ("index_tasks",    "sinbyte_submitted_count", "INTEGER DEFAULT 0"),
        ("index_tasks",    "next_check_at",           "TIMESTAMP"),
        ("index_tasks",    "last_checked_at",         "TIMESTAMP"),
        ("index_tasks",    "submitted_at",            "TIMESTAMP"),
        ("index_tasks",    "check_count",             "INTEGER DEFAULT 0"),
        ("index_tasks",    "sinbyte_task_id",         "TEXT"),
        ("users",          "reset_token",             "TEXT"),
        ("users",          "reset_token_expires",     "TIMESTAMP"),
        ("authors",        "language",                "TEXT DEFAULT 'vi'"),
    ]
    with engine.connect() as conn:
        for table, column, definition in new_columns:
            try:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
                ))
                conn.commit()
            except Exception:
                pass


def _run_migrations_sqlite():
    new_columns = [
        ("projects",       "custom_labels",       "TEXT DEFAULT '[]'"),
        ("projects",       "user_id",             "INTEGER REFERENCES users(id)"),
        ("projects",       "ai_model",            "TEXT DEFAULT 'meta-llama/llama-3.1-8b-instruct:free'"),
        ("articles",       "labels",              "TEXT DEFAULT '[]'"),
        ("articles",       "author_id",           "INTEGER REFERENCES authors(id)"),
        ("articles",       "content_angle_id",    "INTEGER REFERENCES content_angles(id)"),
        ("articles",       "retry_count",         "INTEGER DEFAULT 0"),
        ("articles",       "error_message",       "TEXT"),
        ("articles",       "published_at",        "DATETIME"),
        ("articles",       "url",                 "TEXT"),
        ("articles",       "blogger_post_id",     "TEXT"),
        ("articles",       "language",            "TEXT DEFAULT 'vi'"),
        ("blogspot_sites", "user_id",             "INTEGER REFERENCES users(id)"),
        ("blogspot_sites", "default_language",    "TEXT DEFAULT 'vi'"),
        ("blogspot_sites", "is_active",           "BOOLEAN DEFAULT 1"),
        ("blogspot_sites", "platform",            "TEXT DEFAULT 'blogspot'"),
        ("blogspot_sites", "platform_account_id", "INTEGER REFERENCES platform_accounts(id)"),
        ("blogspot_sites", "category",            "TEXT"),
        ("google_accounts", "user_id",            "INTEGER REFERENCES users(id)"),
        ("project_sites",  "language",            "TEXT DEFAULT 'vi'"),
        ("project_sites",  "articles_today",      "INTEGER DEFAULT 0"),
        ("project_sites",  "last_count_reset",    "DATE"),
        ("project_sites",  "last_published_at",   "DATETIME"),
        ("subscriptions",  "projects_limit",          "INTEGER DEFAULT 1"),
        ("subscriptions",  "sites_limit",             "INTEGER DEFAULT 1"),
        ("subscriptions",  "articles_per_day_limit",  "INTEGER DEFAULT 5"),
        ("index_tasks",    "sinbyte_submitted_count", "INTEGER DEFAULT 0"),
        ("index_tasks",    "next_check_at",           "DATETIME"),
        ("index_tasks",    "last_checked_at",         "DATETIME"),
        ("index_tasks",    "submitted_at",            "DATETIME"),
        ("index_tasks",    "check_count",             "INTEGER DEFAULT 0"),
        ("index_tasks",    "sinbyte_task_id",         "TEXT"),
        ("users",          "reset_token",             "TEXT"),
        ("users",          "reset_token_expires",     "DATETIME"),
        ("authors",        "language",                "TEXT DEFAULT 'vi'"),
    ]
    with engine.connect() as conn:
        for table, column, definition in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                conn.commit()
            except Exception:
                pass
    _fix_blogspot_sites_nullable()
