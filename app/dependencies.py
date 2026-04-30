from typing import Optional
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, Subscription


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id, User.is_active == True).first()


def get_subscription(user: Optional[User] = Depends(get_current_user)) -> Optional[Subscription]:
    return user.subscription if user else None


def can_create_project(user: User, db: Session) -> tuple[bool, str]:
    sub = user.subscription
    if not sub:
        return False, "Không có gói dịch vụ"
    if not sub.is_active_plan:
        return False, "Gói dịch vụ đã hết hạn. Vui lòng gia hạn."
    if sub.projects_limit is None:
        return True, ""
    from .models import Project
    current = db.query(Project).filter(Project.user_id == user.id).count()
    if current >= sub.projects_limit:
        return False, f"Đã đạt giới hạn {sub.projects_limit} dự án của gói {sub.plan.upper()}. Nâng cấp để tạo thêm."
    return True, ""


def can_add_site(user: User, db: Session) -> tuple[bool, str]:
    sub = user.subscription
    if not sub or not sub.is_active_plan:
        return False, "Gói dịch vụ đã hết hạn."
    if sub.sites_limit is None:
        return True, ""
    from .models import BlogspotSite
    current = db.query(BlogspotSite).filter(BlogspotSite.user_id == user.id).count()
    if current >= sub.sites_limit:
        return False, f"Đã đạt giới hạn {sub.sites_limit} website của gói {sub.plan.upper()}. Nâng cấp để thêm."
    return True, ""
