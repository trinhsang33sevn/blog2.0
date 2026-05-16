import json
import logging
import random
import socket
from datetime import datetime, timedelta, date
from threading import Thread

import psutil

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Project, ProjectSite, KeywordCluster, Article, IndexTask, BlogspotSite, GoogleAccount, PlatformAccount
from . import openrouter, blogger, wordpress, tumblr, hashnode, sinbyte, index_checker, image_service, agent_service, wordpress_selfhosted as wp_sh, index_push

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler = None

# ─── PostgreSQL Advisory Locks (per-project) ─────────────────────────────────
# Lock namespace — pg_try_advisory_lock(class, id) uses two int4 values.
# class=1 → clustering jobs, class=2 → publishing jobs
# Each project gets its own lock so different users' projects run in parallel.

_LOCK_CLASS_CLUSTER  = 1
_LOCK_CLASS_PUBLISH  = 2


def _try_project_lock(db: Session, lock_class: int, project_id: int) -> bool:
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_lock(:c, :p)"),
            {"c": lock_class, "p": project_id},
        ).scalar()
    )


def _release_project_lock(db: Session, lock_class: int, project_id: int) -> None:
    try:
        db.execute(
            text("SELECT pg_advisory_unlock(:c, :p)"),
            {"c": lock_class, "p": project_id},
        )
    except Exception:
        pass


def _spin(text: str) -> str:
    """Resolve {A|B|C} spin syntax — randomly picks one option per group."""
    import re
    return re.sub(
        r'\{([^{}]+)\}',
        lambda m: random.choice([o.strip() for o in m.group(1).split("|")]),
        text,
    )

# ─── Writing Constants ────────────────────────────────────────────────────────

MAX_WRITE_RETRIES = 3        # số lần retry tối đa cho bài failed
STUCK_WRITING_MINUTES = 20   # reset bài kẹt ở "writing" sau N phút
MIN_CONTENT_LENGTH = 1500    # ký tự tối thiểu cho nội dung thực sự


def get_db() -> Session:
    return SessionLocal()


# ─── Article Writing ──────────────────────────────────────────────────────────

def _validate_article(title: str, content: str) -> None:
    """Kiểm tra bài viết có nội dung thật sự hay không. Raise nếu không đạt."""
    if not title or len(title.strip()) < 10:
        raise ValueError(f"Tiêu đề quá ngắn hoặc rỗng: '{title}'")
    if not content or len(content) < MIN_CONTENT_LENGTH:
        raise ValueError(
            f"Nội dung quá ngắn: {len(content or '')} ký tự (tối thiểu {MIN_CONTENT_LENGTH})"
        )
    content_lower = content.lower()
    if "<p>" not in content_lower and "<p " not in content_lower:
        raise ValueError("Nội dung thiếu thẻ đoạn văn <p>")
    if "<h2" not in content_lower:
        raise ValueError("Nội dung thiếu tiêu đề mục <h2>")


def _recover_stuck_articles(db: Session) -> None:
    """Reset bài bị kẹt ở 'writing' quá lâu về 'pending' để xử lý lại."""
    cutoff = datetime.utcnow() - timedelta(minutes=STUCK_WRITING_MINUTES)
    stuck = (
        db.query(Article)
        .filter(Article.status == "writing", Article.updated_at <= cutoff)
        .limit(200)
        .all()
    )
    for a in stuck:
        a.status = "pending"
        a.error_message = f"Tự động khôi phục sau {STUCK_WRITING_MINUTES} phút kẹt"
        logger.warning(f"Khôi phục bài kẹt article_id={a.id}")
    if stuck:
        db.commit()


def _get_running_project_ids(db: Session) -> list[int]:
    from ..models import User, Subscription
    now = datetime.utcnow()
    projects = (
        db.query(Project)
        .join(User, Project.user_id == User.id)
        .join(Subscription, User.id == Subscription.user_id)
        .filter(
            Project.status == "running",
            User.is_active == True,
            (Subscription.expires_at.is_(None)) | (Subscription.expires_at > now),
        )
        .all()
    )
    return [p.id for p in projects]


def _get_round_robin_pending(db: Session, running_ids: list[int], limit: int = 5) -> list:
    """Lấy bài pending theo vòng xoay site: A1, B1, C1, A2, B2, C2, ..."""
    if not running_ids:
        return []

    all_pending = (
        db.query(Article)
        .filter(Article.status == "pending", Article.project_id.in_(running_ids))
        .order_by(Article.site_id, Article.created_at)
        .all()
    )

    by_site: dict[int, list] = {}
    for article in all_pending:
        by_site.setdefault(article.site_id, []).append(article)

    result = []
    site_queues = list(by_site.values())
    idx = 0
    while len(result) < limit and any(site_queues):
        queue = site_queues[idx % len(site_queues)]
        if queue:
            result.append(queue.pop(0))
        idx += 1
        if idx >= len(site_queues) * (limit + 1):
            break

    return result[:limit]


def _get_retryable_failed(db: Session, running_ids: list[int], limit: int = 5) -> list:
    """Lấy bài failed còn dưới MAX_WRITE_RETRIES để tự động thử lại."""
    if not running_ids:
        return []

    return (
        db.query(Article)
        .filter(
            Article.status == "failed",
            Article.retry_count < MAX_WRITE_RETRIES,
            Article.project_id.in_(running_ids),
        )
        .order_by(Article.updated_at)
        .limit(limit)
        .all()
    )


def _write_single_article(db: Session, article: Article) -> None:
    """Viết nội dung cho một bài. Raise exception nếu thất bại hoặc nội dung không đạt."""
    cluster = article.cluster
    project = article.project

    if not cluster:
        raise ValueError("Bài viết không có cluster từ khóa")
    if not cluster.keywords:
        raise ValueError("Cluster không có từ khóa nào")

    existing_titles = [
        a.title for a in cluster.articles
        if a.id != article.id and a.title
    ]
    keywords = [kw.keyword for kw in cluster.keywords]
    backlinks = [
        {**bl, "anchor": _spin(bl["anchor"])} if bl.get("anchor") else bl
        for bl in json.loads(project.backlinks or "[]")
    ]

    published_on_site = (
        db.query(Article)
        .filter(
            Article.site_id == article.site_id,
            Article.status == "published",
            Article.url.isnot(None),
            Article.title.isnot(None),
        )
        .order_by(Article.published_at.desc())
        .limit(20)
        .all()
    )
    internal_links = [{"title": a.title, "url": a.url} for a in published_on_site]

    author, angle = agent_service.assign_agent(db, article, cluster)
    if author:
        article.author_id = author.id
    if angle:
        article.content_angle_id = angle.id
    # Commit ngay để release write lock trước khi gọi AI (có thể mất 60-120s)
    db.commit()

    # ── Gọi AI — không giữ write lock ────────────────────────────────────────
    article_id   = article.id
    cluster_id   = cluster.id
    user_id      = project.user_id
    ai_model     = project.ai_model
    language     = article.language

    # ── Bước 0: Research Brief ────────────────────────────────────────────────
    # Cache per cluster: nếu đã có research (JSON) cho cluster này thì tái dùng,
    # tránh gọi AI thêm lần nữa cho các bài cùng cluster (ví dụ nhiều site).
    research_brief = None
    fresh_cluster = db.get(KeywordCluster, cluster_id)
    if fresh_cluster and fresh_cluster.intent_analysis:
        try:
            cached = json.loads(fresh_cluster.intent_analysis)
            if isinstance(cached, dict) and "intent_type" in cached:
                research_brief = cached
                logger.info(f"Cluster {cluster_id}: reusing cached research brief")
        except (json.JSONDecodeError, TypeError, ValueError):
            pass  # old plain-text format → sẽ research lại

    if research_brief is None:
        try:
            research_brief = openrouter.analyze_search_intent_and_research(
                db=db,
                keywords=keywords,
                cluster_name=cluster.cluster_name,
                language=language,
                model=ai_model,
                user_id=user_id,
            )
            # Lưu cache vào cluster để các bài cùng cluster tái dùng
            c = db.get(KeywordCluster, cluster_id)
            if c and c.intent_analysis is None:
                c.intent_analysis = json.dumps(research_brief, ensure_ascii=False)
                db.commit()
            logger.info(f"Cluster {cluster_id}: research brief generated OK")
        except Exception as exc:
            logger.warning(f"Cluster {cluster_id}: research brief failed ({exc}), writing without it")

    # ── Bước 1: Viết bài với research context ─────────────────────────────────
    result = openrouter.analyze_intent_and_write_article(
        db=db,
        keywords=keywords,
        cluster_name=cluster.cluster_name,
        language=language,
        backlinks=backlinks,
        model=ai_model,
        existing_titles=existing_titles,
        internal_links=internal_links,
        author_persona={"name": author.name, "bio": author.bio, "writing_style": author.writing_style} if author else None,
        content_angle={"name": angle.name, "description": angle.description} if angle else None,
        research_brief=research_brief,
        user_id=user_id,
    )

    title = (result.get("title") or "").strip()
    raw_content = (result.get("content") or "").strip()

    if not title:
        raise ValueError("AI trả về tiêu đề rỗng")
    if not raw_content:
        raise ValueError("AI trả về nội dung rỗng")

    # Tầng 1: thay thế cụm từ AI điển hình
    processed = openrouter._replace_ai_phrases(raw_content)

    # Tầng 2: AI humanize — cũng không giữ lock
    processed = openrouter.humanize_article(
        db=db,
        content=processed,
        language=language,
        model=ai_model,
        user_id=user_id,
    )

    # Kiểm tra nội dung trước khi chèn ảnh
    _validate_article(title, processed)

    # Chèn ảnh vào bài viết (HTTP call, không cần DB lock)
    pixabay_key = openrouter.get_setting(db, "pixabay_api_key", user_id=user_id)
    imgbb_key   = openrouter.get_setting(db, "imgbb_api_key",   user_id=user_id)
    final_content = image_service.insert_images_into_content(
        content=processed,
        title=title,
        image_prompt=result.get("image_prompt", ""),
        image_queries=result.get("image_queries", []),
        pixabay_api_key=pixabay_key,
        imgbb_api_key=imgbb_key,
    )

    # ── Ghi kết quả vào DB — write lock ngắn ─────────────────────────────────
    # Reload article từ DB để tránh stale state sau khi commit ở trên
    article = db.get(Article, article_id)
    if not article:
        raise ValueError(f"Article {article_id} không tìm thấy sau khi xử lý AI")

    ai_labels = result.get("labels", [])
    if ai_labels:
        article.labels = json.dumps(ai_labels, ensure_ascii=False)

    # Chỉ lưu intent_analysis đơn giản nếu chưa có research brief JSON
    if research_brief is None:
        c = db.get(KeywordCluster, cluster_id)
        if c and c.intent_analysis is None:
            c.intent_analysis = result.get("intent_analysis", "")

    attempts = (article.retry_count or 0) + 1

    # Kiểm tra nội dung trước khi lưu — nếu là thinking text thì retry
    if _is_broken_content(final_content):
        article.retry_count   = attempts
        article.status        = "failed" if attempts >= 3 else "pending"
        article.error_message = "Broken content (thinking model output) — requeued"
        db.commit()
        logger.warning(f"Article {article_id} attempt={attempts}: broken content detected, requeued (status={article.status})")
        return

    article.title        = title
    article.content      = final_content
    article.status       = "ready"
    article.retry_count  = 0
    article.error_message = None
    db.commit()
    logger.info(f"Article {article_id} written OK (attempt={attempts}): {title}")


def process_writing_queue():
    """
    Xử lý queue viết bài:
    1. Khôi phục bài kẹt 'writing'
    2. Lấy bài 'pending' (round-robin theo site)
    3. Nếu không đủ, lấy thêm bài 'failed' còn trong giới hạn retry
    4. Viết từng bài, validate nội dung, tự retry nếu lỗi
    """
    db = get_db()
    try:
        # Bước 1: Khôi phục bài kẹt
        _recover_stuck_articles(db)

        # Bước 2+3: Lấy bài cần xử lý (query running_ids một lần duy nhất)
        running_ids = _get_running_project_ids(db)
        articles = _get_round_robin_pending(db, running_ids, limit=5)
        if len(articles) < 5:
            articles += _get_retryable_failed(db, running_ids, limit=5 - len(articles))

        if not articles:
            return

        for article in articles:
            try:
                article.status = "writing"
                article.updated_at = datetime.utcnow()
                db.commit()

                _write_single_article(db, article)

            except Exception as e:
                logger.error(f"Error writing article {article.id} (retry={article.retry_count}): {e}")
                article.status = "failed"
                article.error_message = str(e)[:500]
                article.retry_count = (article.retry_count or 0) + 1
                article.updated_at = datetime.utcnow()
                db.commit()

                if article.retry_count >= MAX_WRITE_RETRIES:
                    logger.error(
                        f"Article {article.id} đã thất bại {article.retry_count} lần, "
                        f"dừng retry. Lỗi cuối: {e}"
                    )
    finally:
        db.close()


def write_articles_now(article_ids: list[int]) -> None:
    """
    Viết nội dung ngay lập tức cho các bài trong danh sách article_ids.
    Gọi trong background thread — không chặn request.
    Sau khi viết xong, status → 'ready', scheduler sẽ đăng theo lịch bình thường.
    """
    db = get_db()
    try:
        articles = (
            db.query(Article)
            .filter(Article.id.in_(article_ids), Article.status.in_(("pending", "failed")))
            .all()
        )
        for article in articles:
            article_id = article.id
            try:
                article.status = "writing"
                article.updated_at = datetime.utcnow()
                db.commit()
                _write_single_article(db, article)
                logger.info(f"[immediate-write] Article {article_id} written OK")
            except Exception as e:
                logger.error(f"[immediate-write] Article {article_id} failed: {e}")
                fresh = db.get(Article, article_id)
                if fresh:
                    fresh.status = "failed"
                    fresh.error_message = str(e)[:500]
                    fresh.retry_count = (fresh.retry_count or 0) + 1
                    fresh.updated_at = datetime.utcnow()
                    db.commit()
    finally:
        db.close()


# ─── Article Publishing ───────────────────────────────────────────────────────

def reset_daily_counts(db: Session):
    """Reset articles_today counter for all project sites at the start of each day."""
    today = date.today()
    project_sites = db.query(ProjectSite).all()
    for ps in project_sites:
        if ps.last_count_reset != today:
            ps.articles_today = 0
            ps.last_count_reset = today
    db.commit()


def _publish_article(db: Session, project, ps, article) -> None:
    """Đăng một bài lên platform tương ứng và cập nhật trạng thái."""
    site     = db.query(BlogspotSite).filter(BlogspotSite.id == ps.site_id).first()
    platform = getattr(site, "platform", None) or "blogspot"

    custom = json.loads(project.custom_labels or "[]")
    if custom:
        labels = custom
    elif article.labels:
        labels = json.loads(article.labels)
    elif article.cluster:
        labels = [article.cluster.cluster_name]
    else:
        labels = None

    if platform == "blogspot":
        account = db.query(GoogleAccount).filter(GoogleAccount.id == site.account_id).first()
        if not account:
            raise ValueError(f"Google account cho site {site.id} không tìm thấy (đã bị xóa?)")
        post_data = blogger.publish_post(
            db=db, account=account, blog_id=site.blog_id,
            title=article.title, content=article.content, labels=labels or None,
        )
    elif platform == "wordpress":
        pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
        if not pa:
            raise ValueError(f"WordPress account cho site {site.id} không tìm thấy (đã bị xóa?)")
        content = image_service.rehost_images_for_platform(
            article.content, "wordpress", access_token=pa.access_token, site_id=site.blog_id
        )
        if content != article.content:
            article.content = content
            db.commit()
        post_data = wordpress.publish_post(pa.access_token, site.blog_id, article.title, content, labels)
    elif platform == "tumblr":
        pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
        if not pa:
            raise ValueError(f"Tumblr account cho site {site.id} không tìm thấy (đã bị xóa?)")
        token     = tumblr.refresh_access_token(db, pa)
        post_data = tumblr.publish_post(token, site.blog_id, article.title, article.content, labels)
    elif platform == "hashnode":
        pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
        if not pa:
            raise ValueError(f"Hashnode account cho site {site.id} không tìm thấy (đã bị xóa?)")
        post_data = hashnode.publish_post(pa.access_token, site.blog_id, article.title, article.content, labels)
    elif platform == "wordpress_selfhosted":
        pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
        if not pa:
            raise ValueError(f"WP Self-hosted account cho site {site.id} không tìm thấy (đã bị xóa?)")
        content = image_service.rehost_images_for_platform(
            article.content, "wordpress_selfhosted",
            site_url=pa.refresh_token, username=pa.name, app_password=pa.access_token,
        )
        if content != article.content:
            article.content = content
            db.commit()
        post_data = wp_sh.publish_post(pa.refresh_token, pa.name, pa.access_token, article.title, content, labels)
    else:
        raise ValueError(f"Platform không được hỗ trợ: {platform}")

    now = datetime.utcnow()
    article.url             = post_data.get("url", "")
    article.blogger_post_id = post_data.get("id", "")
    article.status          = "published"
    article.published_at    = now
    ps.articles_today      += 1
    ps.last_published_at    = now
    # Tính thời điểm đăng bài tiếp theo cho CÙNG site này (1 lần, cố định)
    next_gap = random.randint(project.min_interval_minutes, project.max_interval_minutes)
    ps.next_publish_at = now + timedelta(minutes=next_gap)
    db.commit()

    db.add(IndexTask(
        article_id=article.id,
        url=article.url,
        status="pending",
        next_check_at=datetime.utcnow() + timedelta(days=7),
    ))
    db.commit()
    logger.info(f"Published article {article.id} [{platform}]: {article.url}")

    # Push index notifications immediately after publish (non-blocking best-effort)
    if article.url:
        try:
            index_push.push_all(
                url=article.url,
                db=db,
                user_id=project.user_id,
                platform=platform,
                blog_name=site.blog_name or "",
            )
        except Exception as e:
            logger.warning("index_push.push_all error: %s", e)


def publish_ready_articles():
    """
    Viết và đăng bài theo lịch (just-in-time).
    Khi tới giờ đăng: viết bài → đăng ngay. Nếu viết lỗi → thử lại lần sau.
    Per-project advisory lock: nhiều user chạy song song, không ai block ai.
    """
    db = get_db()
    try:
        reset_daily_counts(db)
        _recover_stuck_articles(db)
        now = datetime.utcnow()

        running_projects = db.query(Project).filter(Project.status == "running").all()

        for project in running_projects:
            if not _try_project_lock(db, _LOCK_CLASS_PUBLISH, project.id):
                continue  # worker khác đang xử lý project này
            try:
                for ps in project.project_sites:
                    if ps.articles_today >= project.articles_per_day:
                        continue

                    if ps.next_publish_at and now < ps.next_publish_at:
                        continue

                    article = (
                        db.query(Article)
                        .filter(
                            Article.site_id == ps.site_id,
                            Article.project_id == project.id,
                            Article.status == "ready",
                        )
                        .order_by(Article.created_at)
                        .first()
                    )

                    if not article:
                        article = (
                            db.query(Article)
                            .filter(
                                Article.site_id == ps.site_id,
                                Article.project_id == project.id,
                                Article.status == "pending",
                            )
                            .order_by(Article.created_at)
                            .first()
                        )

                    if not article:
                        article = (
                            db.query(Article)
                            .filter(
                                Article.site_id == ps.site_id,
                                Article.project_id == project.id,
                                Article.status == "failed",
                                Article.retry_count < MAX_WRITE_RETRIES,
                            )
                            .order_by(Article.updated_at)
                            .first()
                        )

                    if not article:
                        continue

                    if article.status != "ready":
                        try:
                            article.status = "writing"
                            article.updated_at = datetime.utcnow()
                            db.commit()
                            _write_single_article(db, article)
                        except Exception as e:
                            logger.error(f"Write failed for article {article.id}: {e}")
                            article.status = "failed"
                            article.error_message = str(e)[:500]
                            article.retry_count = (article.retry_count or 0) + 1
                            article.updated_at = datetime.utcnow()
                            db.commit()
                            continue

                    try:
                        _publish_article(db, project, ps, article)
                    except Exception as e:
                        logger.error(f"Publish failed for article {article.id}: {e}")
                        article.status = "failed"
                        article.error_message = str(e)[:500]
                        article.retry_count = (article.retry_count or 0) + 1
                        article.updated_at = datetime.utcnow()
                        db.commit()
            finally:
                _release_project_lock(db, _LOCK_CLASS_PUBLISH, project.id)

    finally:
        db.close()


# ─── Keyword Clustering ───────────────────────────────────────────────────────

def process_keyword_clustering():
    """Cluster keywords for projects that have pending keywords.
    Mỗi lần chạy xử lý tối đa 1 batch (60 từ khóa) mỗi project.
    Kết quả được lưu ngay sau mỗi batch — không mất dữ liệu nếu batch sau thất bại.
    Per-project advisory lock: nhiều user chạy song song, không ai block ai.
    """
    from ..models import Keyword
    _BATCH = 60  # từ khóa tối đa mỗi lần chạy scheduler
    db = get_db()
    try:
        projects_with_pending = (
            db.query(Project)
            .filter(Project.status == "running")
            .all()
        )
        for project in projects_with_pending:
            pending_kws = (
                db.query(Keyword)
                .filter(Keyword.project_id == project.id, Keyword.status == "pending")
                .limit(_BATCH)
                .all()
            )
            if not pending_kws:
                continue

            if not _try_project_lock(db, _LOCK_CLASS_CLUSTER, project.id):
                continue  # worker khác đang xử lý project này

            kw_texts = [kw.keyword for kw in pending_kws]
            kw_map   = {kw.keyword: kw for kw in pending_kws}
            try:
                clusters_data = openrouter._cluster_batch(db, kw_texts, openrouter.get_setting(db, "openrouter_model", openrouter.DEFAULT_OR_MODEL, user_id=project.user_id), user_id=project.user_id)

                for cluster_data in clusters_data:
                    cluster = KeywordCluster(
                        project_id=project.id,
                        cluster_name=cluster_data["name"],
                        status="pending",
                    )
                    db.add(cluster)
                    db.flush()

                    for kw_text in cluster_data["keywords"]:
                        kw_obj = kw_map.get(kw_text)
                        if kw_obj:
                            kw_obj.cluster_id = cluster.id
                            kw_obj.status = "clustered"

                    for ps in project.project_sites:
                        article = Article(
                            cluster_id=cluster.id,
                            site_id=ps.site_id,
                            project_id=project.id,
                            language=ps.language,
                            status="pending",
                        )
                        db.add(article)

                db.commit()
                remaining = db.query(Keyword).filter(Keyword.project_id == project.id, Keyword.status == "pending").count()
                logger.info(f"Clustered {len(kw_texts)} keywords into {len(clusters_data)} clusters for project {project.id} (còn {remaining} kw pending)")

                Thread(target=process_writing_queue, daemon=True).start()

            except Exception as e:
                logger.error(f"Error clustering keywords for project {project.id}: {e}")
                db.rollback()
            finally:
                _release_project_lock(db, _LOCK_CLASS_CLUSTER, project.id)

    finally:
        db.close()


# ─── Sinbyte Submission ───────────────────────────────────────────────────────

def submit_new_urls_to_sinbyte():
    """Submit newly published article URLs to Sinbyte (nếu có key), rồi chuyển sang submitted."""
    db = get_db()
    try:
        pending_tasks = (
            db.query(IndexTask)
            .filter(IndexTask.status == "pending", IndexTask.sinbyte_submitted_count == 0)
            .all()
        )
        if not pending_tasks:
            return

        urls = [t.url for t in pending_tasks if t.url]
        if not urls:
            return

        sinbyte_key = openrouter.get_setting(db, "sinbyte_api_key")
        task_id = ""

        if sinbyte_key and sinbyte_key.strip():
            try:
                task_name = f"AutoBlogspot-{datetime.utcnow().strftime('%Y%m%d-%H%M')}"
                result = sinbyte.submit_urls(db, task_name, urls)
                task_id = str(result.get("id", ""))
                logger.info(f"Submitted {len(urls)} URLs to Sinbyte, task_id={task_id}")
            except Exception as e:
                logger.warning(f"Sinbyte submit failed ({e}), bỏ qua Sinbyte, vẫn theo dõi Google Index")
        else:
            logger.info(f"Sinbyte chưa cấu hình — bỏ qua, {len(urls)} URLs chuyển sang theo dõi Google Index trực tiếp")

        # Dù có Sinbyte hay không, vẫn chuyển sang submitted để check_index_status() hoạt động
        now = datetime.utcnow()
        for task in pending_tasks:
            task.status = "submitted"
            task.submitted_at = now
            if task_id:
                task.sinbyte_task_id = task_id
                task.sinbyte_submitted_count = 1

        db.commit()

    finally:
        db.close()


# ─── System Health Monitoring ────────────────────────────────────────────────

# Rate-limit: one alert per type per cooldown window
_last_alert_sent: dict[str, datetime] = {}
ALERT_COOLDOWN_HOURS = 6


def check_system_health():
    """Check disk/CPU/RAM against admin thresholds and send email if exceeded."""
    db = get_db()
    try:
        from .email_service import send_system_alert
        from . import openrouter as _or

        alert_email = _or.get_setting(db, "alert_email") or ""
        if not alert_email:
            return

        disk_thresh = int(_or.get_setting(db, "disk_alert_pct") or "80")
        cpu_thresh  = int(_or.get_setting(db, "cpu_alert_pct")  or "90")
        ram_thresh  = int(_or.get_setting(db, "ram_alert_pct")  or "90")

        disk = psutil.disk_usage("/")
        cpu  = psutil.cpu_percent(interval=1)
        ram  = psutil.virtual_memory()
        now  = datetime.utcnow()

        def _cooldown_ok(key: str) -> bool:
            last = _last_alert_sent.get(key)
            return last is None or (now - last).total_seconds() > ALERT_COOLDOWN_HOURS * 3600

        triggered = []
        if disk.percent >= disk_thresh and _cooldown_ok("disk"):
            triggered.append({
                "icon": "💾", "label": "Ổ cứng",
                "value": f"{disk.percent:.1f}% (ngưỡng {disk_thresh}%)",
                "action": "Chạy: sudo docker builder prune -af",
            })
            _last_alert_sent["disk"] = now

        if cpu >= cpu_thresh and _cooldown_ok("cpu"):
            triggered.append({
                "icon": "🖥️", "label": "CPU",
                "value": f"{cpu:.1f}% (ngưỡng {cpu_thresh}%)",
                "action": "Kiểm tra tiến trình nặng",
            })
            _last_alert_sent["cpu"] = now

        if ram.percent >= ram_thresh and _cooldown_ok("ram"):
            triggered.append({
                "icon": "🧠", "label": "RAM",
                "value": f"{ram.percent:.1f}% (ngưỡng {ram_thresh}%)",
                "action": "Khởi động lại app container",
            })
            _last_alert_sent["ram"] = now

        if triggered:
            stats = {
                "hostname":     socket.gethostname(),
                "cpu_pct":      cpu,
                "cpu_cores":    psutil.cpu_count(),
                "mem_pct":      ram.percent,
                "mem_used_gb":  round(ram.used  / 1024**3, 1),
                "mem_total_gb": round(ram.total / 1024**3, 1),
                "disk_pct":     disk.percent,
                "disk_used_gb": round(disk.used  / 1024**3, 1),
                "disk_total_gb":round(disk.total / 1024**3, 1),
            }
            send_system_alert(alert_email, triggered, stats)
            logger.warning(f"System alert sent to {alert_email}: {[a['label'] for a in triggered]}")
    except Exception as exc:
        logger.error(f"check_system_health error: {exc}")
    finally:
        db.close()


# ─── Index Checking ───────────────────────────────────────────────────────────

def check_index_status():
    """Check index status for articles due for checking."""
    db = get_db()
    try:
        now = datetime.utcnow()
        due_tasks = (
            db.query(IndexTask)
            .filter(
                IndexTask.status == "submitted",
                IndexTask.next_check_at <= now,
            )
            .limit(10)  # Check max 10 per run to avoid Google blocking
            .all()
        )

        for task in due_tasks:
            try:
                is_indexed = index_checker.check_google_index(task.url)
                task.last_checked_at = datetime.utcnow()
                task.check_count += 1

                if is_indexed:
                    task.status = "indexed"
                    logger.info(f"URL indexed: {task.url}")
                    if task.article:
                        agent_service.update_feedback(db, task.article, indexed=True)
                else:
                    _handle_not_indexed(db, task)

                db.commit()

            except Exception as e:
                logger.error(f"Error checking index for {task.url}: {e}")

    finally:
        db.close()


def _handle_not_indexed(db: Session, task: IndexTask):
    """Handle article not yet indexed according to retry logic."""
    check_count = task.check_count

    if check_count == 1:
        # First check failed → re-submit to Sinbyte, check after 3 days
        _resubmit_to_sinbyte(db, task)
        task.next_check_at = datetime.utcnow() + timedelta(days=3)
        logger.info(f"Not indexed after 7d, resubmitting: {task.url}")

    elif check_count == 2:
        # Second check failed → rewrite article, re-submit, check after 7 days
        _resubmit_to_sinbyte(db, task)
        task.next_check_at = datetime.utcnow() + timedelta(days=7)
        _rewrite_article_for_task(db, task)
        if task.article:
            agent_service.update_feedback(db, task.article, indexed=False)
        logger.info(f"Not indexed after 3d retry, rewriting article: {task.url}")

    else:
        # Subsequent checks → alternate between resubmit (3d) and rewrite+resubmit (7d)
        _resubmit_to_sinbyte(db, task)
        if check_count % 2 == 1:
            task.next_check_at = datetime.utcnow() + timedelta(days=3)
        else:
            task.next_check_at = datetime.utcnow() + timedelta(days=7)
            _rewrite_article_for_task(db, task)


def _resubmit_to_sinbyte(db: Session, task: IndexTask):
    try:
        task_name = f"Retry-{datetime.utcnow().strftime('%Y%m%d')}"
        result = sinbyte.submit_urls(db, task_name, [task.url])
        task.sinbyte_task_id = str(result.get("id", task.sinbyte_task_id))
        task.sinbyte_submitted_count += 1
        task.submitted_at = datetime.utcnow()
    except Exception as e:
        logger.error(f"Error resubmitting to Sinbyte: {e}")


def _rewrite_article_for_task(db: Session, task: IndexTask):
    try:
        article = task.article
        if not article or not article.cluster:
            return

        keywords = [kw.keyword for kw in article.cluster.keywords]
        backlinks = [
            {**bl, "anchor": _spin(bl["anchor"])} if bl.get("anchor") else bl
            for bl in json.loads(article.project.backlinks or "[]")
        ]

        result = openrouter.rewrite_article(
            db=db,
            title=article.title,
            keywords=keywords,
            language=article.language,
            backlinks=backlinks,
            model=article.project.ai_model,
            user_id=article.project.user_id,
        )

        # Update article content
        old_title = article.title
        article.title = result["title"]
        article.content = result["content"]
        article.updated_at = datetime.utcnow()
        db.commit()

        # Update bài trên platform tương ứng
        site     = article.site
        platform = getattr(site, "platform", None) or "blogspot"
        if article.blogger_post_id:
            if platform == "blogspot":
                account = db.query(GoogleAccount).filter(GoogleAccount.id == site.account_id).first()
                if account:
                    blogger.update_post(db=db, account=account, blog_id=site.blog_id,
                                        post_id=article.blogger_post_id,
                                        title=article.title, content=article.content)
            elif platform == "wordpress":
                pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
                if pa:
                    wordpress.update_post(pa.access_token, site.blog_id,
                                          article.blogger_post_id, article.title, article.content)
            elif platform == "tumblr":
                pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
                if pa:
                    token = tumblr.refresh_access_token(db, pa)
                    tumblr.update_post(token, site.blog_id,
                                       article.blogger_post_id, article.title, article.content)
            elif platform == "hashnode":
                pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
                if pa:
                    hashnode.update_post(pa.access_token, article.blogger_post_id,
                                         article.title, article.content)
            elif platform == "wordpress_selfhosted":
                pa = db.query(PlatformAccount).filter(PlatformAccount.id == site.platform_account_id).first()
                if pa:
                    wp_sh.update_post(pa.refresh_token, pa.name, pa.access_token,
                                      article.blogger_post_id, article.title, article.content)
            logger.info(f"Rewrote article {article.id} [{platform}]: '{old_title}' → '{article.title}'")

    except Exception as e:
        logger.error(f"Error rewriting article for task {task.id}: {e}")


# ─── Broken Content Detection ────────────────────────────────────────────────

# Các cụm từ đặc trưng của thinking model / prompt bị lọt vào nội dung bài viết
_BROKEN_PHRASES = [
    "we need to rewrite",
    "we must not add",
    "must keep all html tags",
    "must rewrite content",
    "we need to ensure",
    "we need to produce json",
    "we need to output json",
    "let's parse keywords",
    "must group keywords",
    "we need to cluster",
    "we need to write",
    "must keep same structure",
    "must keep same number of sections",
    "keep all links exactly",
    "language must remain",
    "return only valid json",
    "return only a valid json",
]


def _is_broken_content(content: str) -> bool:
    """Trả về True nếu nội dung là thinking text / prompt thay vì bài viết thật."""
    if not content or len(content) < 50:
        return True
    low = content.lower().strip()
    # Nội dung bài viết thật phải bắt đầu bằng HTML tag
    if not low.startswith("<"):
        for phrase in _BROKEN_PHRASES:
            if phrase in low[:2000]:
                return True
    return False


def detect_and_fix_broken_articles():
    """
    Quét toàn bộ bài viết đã publish hoặc ready có nội dung bị lỗi (thinking model).
    - Nếu đã published: xóa khỏi platform rồi reset về pending để viết lại.
    - Nếu ready (chưa publish): reset về pending luôn.
    """
    db: Session = SessionLocal()
    try:
        candidates = (
            db.query(Article)
            .filter(Article.status.in_(["published", "ready"]))
            .filter(Article.content.isnot(None))
            .all()
        )

        fixed = 0
        for article in candidates:
            if not _is_broken_content(article.content):
                continue

            old_status = article.status
            logger.warning(
                f"[broken-content] Article {article.id} '{article.title}' "
                f"có nội dung lỗi (status={old_status}), reset về pending"
            )

            # Reset về pending để hệ thống viết lại bài mới
            # Bài cũ trên platform (nếu đã publish) cần xóa thủ công
            article.status          = "pending"
            article.content         = None
            article.title           = None
            article.blogger_post_id = None
            article.url             = None
            article.published_at    = None
            article.retry_count     = 0
            article.error_message   = "Broken content detected — requeued"
            fixed += 1

        if fixed:
            db.commit()
            logger.info(f"[broken-content] Đã reset {fixed} bài lỗi về pending")
        else:
            logger.info("[broken-content] Không phát hiện bài lỗi nào")

    except Exception as e:
        logger.error(f"detect_and_fix_broken_articles error: {e}")
        db.rollback()
    finally:
        db.close()


# ─── Auto-refresh free model list ────────────────────────────────────────────

def refresh_openrouter_models_for_all_users():
    """Cập nhật danh sách free models từ OpenRouter API cho tất cả user có API key."""
    db: Session = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT DISTINCT REPLACE(key, '_openrouter_api_key', '') "
            "FROM app_settings WHERE key LIKE '%openrouter_api_key' AND value != ''"
        )).fetchall()

        refreshed = 0
        for (key_prefix,) in rows:
            try:
                user_id = int(key_prefix.lstrip("u")) if key_prefix.startswith("u") else None
                api_key = openrouter.get_setting(db, "openrouter_api_key", user_id=user_id)
                if not api_key:
                    continue
                models = openrouter.fetch_free_models_from_api(api_key)
                openrouter.set_setting(db, "cached_free_models", json.dumps(models), user_id=user_id)
                refreshed += 1
                logger.info(f"Auto-refreshed {len(models)} free models (user_id={user_id})")
            except Exception as e:
                logger.warning(f"refresh_openrouter_models failed for prefix '{key_prefix}': {e}")
        if refreshed == 0:
            logger.info("refresh_openrouter_models: không có user nào có OpenRouter API key")
    except Exception as e:
        logger.error(f"refresh_openrouter_models error: {e}")
    finally:
        db.close()


# ─── Scheduler Init ───────────────────────────────────────────────────────────

def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone="UTC")

    # Cluster keywords every 10 minutes
    _scheduler.add_job(process_keyword_clustering, IntervalTrigger(minutes=10), id="cluster_kw", replace_existing=True)

    # Viết và đăng bài mỗi 5 phút (just-in-time: viết khi tới giờ đăng)
    _scheduler.add_job(publish_ready_articles, IntervalTrigger(minutes=5), id="publish_articles", replace_existing=True)

    # Submit new URLs to Sinbyte every 30 minutes
    _scheduler.add_job(submit_new_urls_to_sinbyte, IntervalTrigger(minutes=30), id="sinbyte_submit", replace_existing=True)

    # Check index status every 6 hours (max 10 URLs per run to avoid blocks)
    _scheduler.add_job(check_index_status, IntervalTrigger(hours=6), id="check_index", replace_existing=True)

    # System health check every 30 minutes — sends email alert if thresholds exceeded
    _scheduler.add_job(check_system_health, IntervalTrigger(minutes=30), id="health_check", replace_existing=True)

    # Cập nhật danh sách free models từ OpenRouter mỗi 24 giờ
    _scheduler.add_job(refresh_openrouter_models_for_all_users, IntervalTrigger(hours=24), id="refresh_or_models", replace_existing=True)

    # Quét và sửa bài viết bị lỗi (thinking model output) mỗi 30 phút
    _scheduler.add_job(detect_and_fix_broken_articles, IntervalTrigger(minutes=30), id="fix_broken", replace_existing=True)

    _scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Scheduler stopped")


def get_scheduler():
    return _scheduler
