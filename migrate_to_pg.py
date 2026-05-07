#!/usr/bin/env python3
"""
Migrate data từ SQLite sang PostgreSQL.

Usage:
    python migrate_to_pg.py

Environment variables required:
    SQLITE_URL   - Path to SQLite file (default: sqlite:///./data/autoblogspot.db)
    DATABASE_URL - PostgreSQL connection URL (required)

Example:
    SQLITE_URL=sqlite:///./data/autoblogspot.db \
    DATABASE_URL=postgresql://autoblogspot:password@localhost:5432/autoblogspot \
    python migrate_to_pg.py
"""

import os
import sys

SQLITE_URL = os.getenv("SQLITE_URL", "sqlite:///./data/autoblogspot.db")
PG_URL = os.getenv("DATABASE_URL", "")

if not PG_URL:
    print("ERROR: DATABASE_URL is not set.")
    print("  export DATABASE_URL=postgresql://user:pass@host:5432/dbname")
    sys.exit(1)

if not PG_URL.startswith("postgresql"):
    print(f"ERROR: DATABASE_URL must be a PostgreSQL URL, got: {PG_URL}")
    sys.exit(1)

from sqlalchemy import create_engine, text, inspect

# Tables ordered by FK dependency (parents before children)
TABLES = [
    "users",
    "authors",
    "content_angles",
    "app_settings",
    "subscriptions",
    "google_accounts",
    "platform_accounts",
    "blogspot_sites",
    "projects",
    "keyword_clusters",
    "keywords",
    "project_sites",
    "articles",
    "index_tasks",
]


def get_sqlite_columns(conn, table: str) -> list[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return [r[1] for r in rows]


def reset_sequence(conn, table: str):
    """Reset PostgreSQL auto-increment sequence to max existing id."""
    try:
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        ))
        conn.commit()
    except Exception:
        pass


def migrate_table(src_conn, dst_conn, table: str):
    # Check if table exists in SQLite
    try:
        rows = src_conn.execute(text(f"SELECT * FROM {table}")).fetchall()
    except Exception as e:
        print(f"  SKIP {table}: {e}")
        return

    if not rows:
        print(f"  SKIP {table}: empty")
        return

    columns = get_sqlite_columns(src_conn, table)
    cols_str = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(f":{c}" for c in columns)

    # Temporarily disable FK checks during load
    ok = 0
    fail = 0
    for row in rows:
        row_dict = dict(zip(columns, row))
        try:
            dst_conn.execute(
                text(
                    f'INSERT INTO "{table}" ({cols_str}) VALUES ({placeholders}) '
                    f"ON CONFLICT DO NOTHING"
                ),
                row_dict,
            )
            ok += 1
        except Exception as e:
            fail += 1
            if fail <= 3:
                print(f"    WARN row {row_dict.get('id', '?')}: {e}")

    dst_conn.commit()
    reset_sequence(dst_conn, table)
    status = f"{ok}/{len(rows)} rows"
    if fail:
        status += f"  ({fail} failed)"
    print(f"  OK  {table}: {status}")


def main():
    print(f"Source : {SQLITE_URL}")
    print(f"Target : {PG_URL}\n")

    sqlite_engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
    )
    pg_engine = create_engine(PG_URL, pool_pre_ping=True)

    # Create all tables in PostgreSQL from SQLAlchemy models
    print("Creating tables in PostgreSQL...")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    os.environ["DATABASE_URL"] = PG_URL  # point settings at PG for init_db

    # Re-import with PG engine
    from app.database import Base
    from app import models  # noqa: F401 — registers all models on Base
    Base.metadata.create_all(bind=pg_engine)
    print("Tables ready.\n")

    print("Migrating data...")
    with sqlite_engine.connect() as src, pg_engine.connect() as dst:
        # Disable FK constraints during bulk load
        dst.execute(text("SET session_replication_role = replica"))
        dst.commit()

        for table in TABLES:
            migrate_table(src, dst, table)

        # Re-enable FK constraints
        dst.execute(text("SET session_replication_role = DEFAULT"))
        dst.commit()

    print("\nMigration complete!")
    print("Next: verify your data, then update DATABASE_URL in .env and restart the app.")


if __name__ == "__main__":
    main()
