import hashlib
import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from ..models import User, Subscription, PLAN_LIMITS


def hash_password(password: str) -> str:
    salt = secrets.token_hex(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
    return f"{salt}:{key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, key_hex = stored_hash.split(":", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def create_user(db: Session, email: str, password: str, full_name: str = "",
                _password_hash: str = "") -> User:
    user = User(
        email=email.strip().lower(),
        password_hash=_password_hash or hash_password(password),
        full_name=full_name.strip(),
        is_admin=db.query(User).count() == 0,  # first user becomes admin
    )
    db.add(user)
    db.flush()

    limits = PLAN_LIMITS["free"]
    sub = Subscription(
        user_id=user.id,
        plan="free",
        status="trial",
        started_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=3),
        projects_limit=limits["projects"],
        sites_limit=limits["sites"],
        articles_per_day_limit=limits["articles_per_day"],
    )
    db.add(sub)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email.strip().lower()).first()


def generate_reset_token(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.utcnow() + timedelta(minutes=30)
    db.commit()
    return token


def validate_reset_token(db: Session, token: str):
    """Return User if token is valid and not expired, else None."""
    user = db.query(User).filter(User.reset_token == token).first()
    if not user or not user.reset_token_expires:
        return None
    if datetime.utcnow() > user.reset_token_expires:
        return None
    return user


def consume_reset_token(db: Session, user: User, new_password: str):
    user.password_hash = hash_password(new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()


def ensure_superadmin(db: Session, email: str, password: str, full_name: str) -> User:
    """Create or update the superadmin account. Called at startup."""
    existing = get_user_by_email(db, email)
    if existing:
        existing.full_name = full_name
        existing.is_admin = True
        existing.is_active = True
        existing.password_hash = hash_password(password)
        db.commit()
        return existing

    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    db.flush()

    sub = Subscription(
        user_id=user.id,
        plan="business",
        status="active",
        started_at=datetime.utcnow(),
        expires_at=None,
        projects_limit=None,
        sites_limit=None,
        articles_per_day_limit=None,
    )
    db.add(sub)
    db.commit()
    db.refresh(user)
    return user


def upgrade_plan(db: Session, user_id: int, plan: str, months: int = 1) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    is_gift = plan in ("gift", "gift_vip")
    now = datetime.utcnow()
    if is_gift:
        expires = None          # gift/gift_vip never expires
        status  = plan          # preserve "gift" or "gift_vip" as status
    else:
        base    = sub.expires_at if (sub and sub.expires_at and sub.expires_at > now) else now
        expires = base + timedelta(days=30 * months)
        status  = "active"

    if sub:
        sub.plan       = plan
        sub.status     = status
        sub.expires_at = expires
        sub.projects_limit         = limits["projects"]
        sub.sites_limit            = limits["sites"]
        sub.articles_per_day_limit = limits["articles_per_day"]
    else:
        sub = Subscription(
            user_id=user_id,
            plan=plan,
            status=status,
            expires_at=expires,
            projects_limit=limits["projects"],
            sites_limit=limits["sites"],
            articles_per_day_limit=limits["articles_per_day"],
        )
        db.add(sub)

    db.commit()
    return sub
