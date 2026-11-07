"""Admin article workflows and their transaction boundaries."""
from datetime import datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.security import hash_password
from app.models import Article
from app.schemas.common import AdminArticleInput
from app.services.taxonomy import lock_category, lock_column, lock_tags


def list_articles(db: Session, status: int | None, keyword: str, category_id: int | None,
                  page: int, page_size: int, trashed: bool = False):
    query = select(Article).where(Article.deleted_at.is_not(None) if trashed else Article.deleted_at.is_(None))
    if not trashed:
        if status is not None and status >= 0:
            query = query.where(Article.status == status)
        if keyword:
            query = query.where(Article.title.contains(keyword))
        if category_id:
            query = query.where(Article.category_id == category_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    order = Article.deleted_at.desc() if trashed else Article.updated_at.desc()
    rows = db.scalars(query.order_by(order, Article.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return rows, total


def get_article(db: Session, article_id: int) -> Article:
    article = db.get(Article, article_id)
    if not article or article.deleted_at:
        raise DomainError("article not found", 404)
    return article


def create_article(db: Session, body: AdminArticleInput, author_id: int) -> Article:
    values = body.model_dump(by_alias=True)
    category_id = int(values.get("categoryId", values.get("category_id", 0)))
    lock_category(db, category_id)
    lock_column(db, values["columnId"], author_id)
    tags = lock_tags(db, values["tagIds"])
    raw_password = values.get("password", "")
    title = values["title"].strip()
    if not title or not values["content"].strip():
        raise DomainError("title and content are required")
    article = Article(
        author_id=author_id, title=title, content=values["content"],
        summary=values.get("summary", ""), category_id=category_id,
        status=values.get("status", 1), cover_image=values.get("coverImage", ""),
        source_url=values.get("sourceUrl", ""),
        password=hash_password(raw_password) if raw_password else "",
        is_top=values.get("isTop", False), is_featured=values.get("isFeatured", False),
        publish_at=_parse_datetime(values.get("publishAt")), column_id=values["columnId"], tags=tags,
    )
    if article.status == 1 and article.publish_at and article.publish_at > datetime.utcnow():
        article.status = 4
    _validate_schedule(article)
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


def update_article(db: Session, article_id: int, body: AdminArticleInput) -> Article:
    column_supplied = "column_id" in body.model_fields_set
    values = body.model_dump(by_alias=True)
    article = get_article(db, article_id)
    lock_category(db, values["categoryId"])
    if column_supplied:
        lock_column(db, values["columnId"], article.author_id)
    tags = lock_tags(db, values["tagIds"])
    if values["clearPassword"] and values["password"]:
        raise DomainError("choose a new password or clear the existing password")
    fields = {"title": "title", "content": "content", "summary": "summary", "coverImage": "cover_image",
              "sourceUrl": "source_url", "categoryId": "category_id", "status": "status",
              "isTop": "is_top", "isFeatured": "is_featured", "publishAt": "publish_at"}
    for key, attr in fields.items():
        if key in values:
            value = _parse_datetime(values[key]) if key == "publishAt" else values[key]
            setattr(article, attr, value)
    if values["clearPassword"]:
        article.password = ""
    elif values["password"]:
        article.password = hash_password(values["password"])
    if column_supplied:
        article.column_id = values["columnId"]
    article.tags = tags
    if article.status == 1 and article.publish_at and article.publish_at > datetime.utcnow():
        article.status = 4
    _validate_schedule(article)
    article.updated_at = datetime.utcnow()
    db.commit()
    return article


def delete_article(db: Session, article_id: int) -> None:
    article = get_article(db, article_id)
    article.deleted_at = datetime.utcnow()
    db.commit()


def restore_article(db: Session, article_id: int) -> Article:
    article = db.get(Article, article_id)
    if not article or not article.deleted_at:
        raise DomainError("trashed article not found", 404)
    article.deleted_at = None
    article.updated_at = datetime.utcnow()
    db.commit()
    return article


def moderate_article(db: Session, article_id: int, approve: bool, reason: str = "") -> Article:
    now = datetime.utcnow()
    values = ({"status": case((Article.publish_at > now, 4), else_=1),
               "publish_at": func.coalesce(Article.publish_at, now), "reject_reason": "", "updated_at": now}
              if approve else {"status": 3, "reject_reason": reason, "updated_at": now})
    changed = db.execute(update(Article).where(
        Article.id == article_id, Article.deleted_at.is_(None), Article.status == 2
    ).values(**values)).rowcount
    if not changed:
        db.rollback()
        if not db.scalar(select(Article.id).where(Article.id == article_id, Article.deleted_at.is_(None))):
            raise DomainError("article not found", 404)
        action = "approved" if approve else "rejected"
        raise DomainError(f"only pending articles can be {action}", 409)
    db.commit()
    return db.get(Article, article_id)


def batch_articles(db: Session, ids: list[int], operation: str) -> int:
    if not operation:
        raise DomainError("batch action is required", 400)
    rows = db.scalars(select(Article).where(Article.id.in_(ids), Article.deleted_at.is_(None))).all()
    for article in rows:
        if operation in ("publish", "approve"):
            article.status = 4 if article.publish_at and article.publish_at > datetime.utcnow() else 1
            if article.status == 1 and not article.publish_at:
                article.publish_at = datetime.utcnow()
        elif operation in ("draft", "setDraft"):
            article.status = 0
        elif operation == "top":
            article.is_top = True
        elif operation == "untop":
            article.is_top = False
        elif operation == "delete":
            article.deleted_at = datetime.utcnow()
        else:
            raise DomainError("unsupported batch operation")
    db.commit()
    return len(rows)


def _validate_schedule(article: Article) -> None:
    if article.status == 4 and not article.publish_at:
        raise DomainError("scheduled articles need a publication time", 400)


def _parse_datetime(value):
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except (AttributeError, ValueError) as exc:
        raise DomainError("invalid publication time", 422) from exc
