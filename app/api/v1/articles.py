from datetime import datetime, timezone
import hashlib
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, load_only
from app.api.deps import current_user, get_db, optional_user, require_editor
from app.core.errors import DomainError, envelope
from app.core.html import article_markdown, sanitize_article_html
from app.core.security import hash_password, verify_password
from app.models import Article, Category, ColumnModel, MetricEvent, Tag, User
from app.repositories.articles import page_public, public_query, visible_conditions
from app.schemas.common import ArticleInput, ArticleUnlockInput
from app.services.view_counts import notify_view
from app.services.taxonomy import lock_category, lock_column, lock_tags

router = APIRouter()


def article_data(a: Article, content=True, public_content=False):
    result = {"id": a.id, "title": a.title, "summary": a.summary, "coverImage": a.cover_image, "sourceUrl": a.source_url, "authorId": a.author_id, "author": {"id": a.author.id, "username": a.author.username, "nickname": a.author.nickname} if a.author else None, "categoryId": a.category_id, "category": {"id": a.category.id, "name": a.category.name} if a.category and not a.category.deleted_at else None, "tags": [{"id": t.id, "name": t.name} for t in a.tags if not t.deleted_at], "status": a.status, "viewCount": a.view_count, "likeCount": a.like_count, "isTop": a.is_top, "isFeatured": a.is_featured, "publishAt": a.publish_at.isoformat() if a.publish_at else None, "createdAt": a.created_at.isoformat()}
    result["columnId"] = a.column_id
    result["updatedAt"] = a.updated_at.isoformat()
    if public_content:
        result["needPassword"] = False
    if content:
        result["content"] = sanitize_article_html(a.content) if public_content else a.content
        if not public_content:
            result["rejectReason"] = a.reject_reason
            result["password"] = ""  # Compatibility with editor forms.
        result["hasPassword"] = bool(a.password)
    return result


@router.get("/articles")
def public_articles(db: Session = Depends(get_db), keyword: str = "", q: str = "", tag: str = "", featured: bool = False, seed: int=1, categoryId: int | None = None, authorId: int | None = None, sort: str = "latest", page: int = Query(1, ge=1), pageSize: int = Query(10, ge=1, le=50)):
    rows, total = page_public(db, page, pageSize, keyword or q, categoryId, authorId, sort, tag, featured, seed)
    return envelope({"list": [article_data(a, False, public_content=True) for a in rows], "total": total, "page": page, "pageSize": pageSize})


@router.get("/articles/hot")
def hot_articles(db: Session = Depends(get_db), limit: int = Query(10, ge=1, le=50)):
    rows = db.scalars(public_query(db).options(defer(Article.content)).order_by(
        Article.like_count.desc(), Article.id.desc()
    ).limit(limit)).all()
    return envelope([article_data(a, False, public_content=True) for a in rows])


@router.get("/articles/{article_id}")
def public_article(article_id: int, db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions()))
    if not a:
        raise DomainError("article not found", 404)
    if a.password:
        return envelope({"id": a.id, "title": a.title, "needPassword": True, "hasPassword": True})
    return envelope(article_data(a, public_content=True))


@router.post("/articles/{article_id}/unlock")
def unlock_article(article_id: int, body: ArticleUnlockInput, db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions()))
    if not a:
        raise DomainError("article not found", 404)
    if a.password and not verify_password(body.password, a.password):
        raise DomainError("article password is incorrect", 403)
    return envelope(article_data(a, public_content=True))


@router.get("/articles/{article_id}/nav")
def article_nav(article_id: int, db: Session = Depends(get_db)):
    current = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions()))
    if not current:
        raise DomainError("article not found", 404)
    candidates = (*visible_conditions(), Article.password == "", Article.category_id == current.category_id)
    previous = db.scalar(select(Article).options(load_only(Article.id, Article.title)).where(
        *candidates, Article.created_at < current.created_at
    ).order_by(Article.created_at.desc(), Article.id.desc()).limit(1))
    following = db.scalar(select(Article).options(load_only(Article.id, Article.title)).where(
        *candidates, Article.created_at > current.created_at
    ).order_by(Article.created_at, Article.id).limit(1))
    related = db.scalars(select(Article).options(load_only(
        Article.id, Article.title, Article.summary, Article.cover_image, Article.view_count
    )).where(
        *candidates, Article.id != current.id
    ).order_by(Article.view_count.desc(), Article.id.desc()).limit(5)).all()
    return envelope({
        "prev": {"id": previous.id, "title": previous.title} if previous else None,
        "next": {"id": following.id, "title": following.title} if following else None,
        "related": [{"id": a.id, "title": a.title, "summary": a.summary,
                     "coverImage": a.cover_image, "viewCount": a.view_count} for a in related],
    })


@router.put("/articles/{article_id}/view")
def add_view(article_id: int, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db),user: User|None=Depends(optional_user)):
    a = db.get(Article, article_id)
    if not a or a.status != 1 or a.deleted_at or (a.publish_at and a.publish_at > datetime.utcnow()):
        raise DomainError("article not found", 404)
    raw=f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    fingerprint=hashlib.sha256(raw.encode()).hexdigest()
    try:
        event = MetricEvent(article_id=article_id, kind="view", fingerprint=fingerprint, counted=False)
        db.add(event)
        db.flush()
        event_id = event.id
        db.commit()
        background_tasks.add_task(notify_view, getattr(request.app.state, "redis", None), event_id)
    except IntegrityError:
        db.rollback()
        if not db.scalar(select(MetricEvent.id).where(MetricEvent.article_id == article_id, MetricEvent.kind == "view", MetricEvent.fingerprint == fingerprint)):
            raise
    pending = select(func.count(MetricEvent.id)).where(MetricEvent.article_id == article_id, MetricEvent.kind == "view", MetricEvent.counted.is_(False)).scalar_subquery()
    count = db.scalar(select(Article.view_count + pending).where(Article.id == article_id))
    return envelope({"viewCount": count})


@router.put("/articles/{article_id}/like")
def like_article(article_id: int, request: Request, db: Session = Depends(get_db),user: User|None=Depends(optional_user)):
    # Serialize toggles on the article row, including when a visitor has not
    # liked it yet. SQLite obtains its write lock from this UPDATE as well.
    locked = db.execute(update(Article).where(Article.id == article_id, *visible_conditions()).values(like_count=Article.like_count, updated_at=Article.updated_at))
    if not locked.rowcount:
        db.rollback()
        raise DomainError("article not found", 404)
    raw=f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    fingerprint=hashlib.sha256(raw.encode()).hexdigest()
    try:
        removed = db.execute(delete(MetricEvent).where(MetricEvent.article_id == article_id, MetricEvent.kind == "like", MetricEvent.fingerprint == fingerprint)).rowcount
        if removed:
            count = case((Article.like_count > 0, Article.like_count - 1), else_=0)
        else:
            db.add(MetricEvent(article_id=article_id, kind="like", fingerprint=fingerprint))
            db.flush()
            count = Article.like_count + 1
        db.execute(update(Article).where(Article.id == article_id).values(like_count=count, updated_at=Article.updated_at))
        like_count = db.scalar(select(Article.like_count).where(Article.id == article_id).with_for_update())
        db.commit()
    except Exception:
        db.rollback()
        raise
    return envelope({"likeCount": like_count, "already": bool(removed)})


@router.get("/articles/{article_id}/export")
def export_article(article_id: int, format: str = "md", db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions(), Article.password == ""))
    if not a:
        raise DomainError("article not found", 404)
    if format != "md":
        raise DomainError("unsupported export format")
    return envelope({"title": a.title, "content": article_markdown(a.content), "format": "md"})


@router.get("/my/articles")
def my_articles(user: User = Depends(current_user), db: Session = Depends(get_db), page: int = Query(1, ge=1), pageSize: int = Query(10, ge=1, le=50), status: int | None = None):
    query = select(Article).where(Article.author_id == user.id, Article.deleted_at.is_(None))
    if status is not None and status >= 0:
        query = query.where(Article.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Article.updated_at.desc()).offset((page - 1) * pageSize).limit(pageSize)).all()
    return envelope({"list": [article_data(a) for a in rows], "total": total, "page": page, "pageSize": pageSize})


@router.post("/my/articles")
def create_my_article(body: ArticleInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lock_category(db, body.category_id)
    lock_column(db, body.column_id, user.id)
    tags = lock_tags(db, body.tag_ids)
    a = Article(author_id=user.id, title=body.title, content=body.content, summary=body.summary, cover_image=body.cover_image, source_url=body.source_url, password=hash_password(body.password) if body.password else "", category_id=body.category_id, column_id=body.column_id, status=2, publish_at=_parse_datetime(body.publish_at))
    a.tags = tags
    db.add(a); db.commit(); db.refresh(a)
    return envelope(article_data(a))


@router.get("/my/articles/{article_id}")
def get_my_article(article_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, Article.author_id == user.id, Article.deleted_at.is_(None)))
    if not a: raise DomainError("article not found", 404)
    return envelope(article_data(a))


@router.put("/my/articles/{article_id}")
def update_my_article(article_id: int, body: ArticleInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, Article.author_id == user.id, Article.deleted_at.is_(None)))
    if not a: raise DomainError("article not found", 404)
    lock_category(db, body.category_id)
    column_id = body.column_id if "column_id" in body.model_fields_set else a.column_id
    lock_column(db, column_id, user.id)
    tags = lock_tags(db, body.tag_ids)
    if body.clear_password and body.password: raise DomainError("choose a new password or clear the existing password")
    for attr, value in (("title", body.title), ("content", body.content), ("summary", body.summary), ("cover_image", body.cover_image), ("source_url", body.source_url), ("category_id", body.category_id), ("column_id", column_id)):
        setattr(a, attr, value)
    if body.clear_password: a.password = ""
    elif body.password: a.password = hash_password(body.password)
    if "publish_at" in body.model_fields_set:
        a.publish_at = _parse_datetime(body.publish_at)
        if a.status == 4 and not a.publish_at:
            a.status = 2
    a.tags = tags
    if a.status in (0,3): a.status = 2
    a.updated_at = datetime.utcnow(); db.commit()
    return envelope(article_data(a))


@router.delete("/my/articles/{article_id}")
def delete_my_article(article_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, Article.author_id == user.id, Article.deleted_at.is_(None)))
    if not a: raise DomainError("article not found", 404)
    a.deleted_at = datetime.utcnow(); db.commit()
    return envelope({})


def _parse_datetime(value):
    if not value: return None
    try:
        parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError,ValueError) as exc:
        raise DomainError("invalid publishAt timestamp") from exc
