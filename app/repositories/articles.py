from sqlalchemy import func, or_, select
from datetime import datetime
from sqlalchemy.orm import Session, selectinload
from app.models import Article, Tag


def visible_conditions():
    return (Article.status == 1, Article.deleted_at.is_(None),
            or_(Article.publish_at.is_(None), Article.publish_at <= datetime.utcnow()))


def public_query(db: Session):
    return select(Article).options(selectinload(Article.author), selectinload(Article.category), selectinload(Article.tags)).where(*visible_conditions(), Article.password == "")


def page_public(db: Session, page: int, size: int, keyword: str = "", category_id: int | None = None, author_id: int | None = None, sort: str = "latest", tag: str = "", featured: bool = False, seed: int = 1):
    query = public_query(db)
    if keyword:
        query = query.where(Article.title.contains(keyword) | Article.summary.contains(keyword))
    if category_id:
        query = query.where(Article.category_id == category_id)
    if author_id:
        query = query.where(Article.author_id == author_id)
    if tag:
        query = query.where(Article.tags.any(Tag.name == tag))
    if featured:
        query = query.where(Article.is_featured.is_(True))
    if featured:
        from app.db.session import engine
        order=func.rand(seed) if engine.dialect.name=="mysql" else func.random()
    else:
        order = Article.view_count.desc() if sort == "hot" else Article.created_at.desc()
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(order, Article.id.desc()).offset((page - 1) * size).limit(size)).all()
    return rows, total
