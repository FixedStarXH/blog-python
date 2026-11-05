from random import Random

from sqlalchemy import func, or_, select
from datetime import datetime
from sqlalchemy.orm import Session, defer, selectinload
from app.models import Article, Tag


def visible_conditions():
    return (Article.status == 1, Article.deleted_at.is_(None),
            or_(Article.publish_at.is_(None), Article.publish_at <= datetime.utcnow()))


def public_query(db: Session):
    return select(Article).options(selectinload(Article.author), selectinload(Article.category), selectinload(Article.tags)).where(*visible_conditions(), Article.password == "")


def page_public(db: Session, page: int, size: int, keyword: str = "", category_id: int | None = None, author_id: int | None = None, sort: str = "latest", tag: str = "", featured: bool = False, seed: int = 1):
    query = public_query(db).options(defer(Article.content))
    if featured:
        query = query.where(Article.is_featured.is_(True))
        # A seeded permutation of IDs keeps page boundaries stable across SQL dialects.
        modulus = 2_147_483_647
        multiplier = Random(seed).randrange(1, modulus)
        order = ((Article.id % modulus) * multiplier) % modulus
    else:
        order = Article.view_count.desc() if sort == "hot" else Article.created_at.desc()
        query = _filter_articles(query, keyword, category_id, author_id, tag)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(order, Article.id.desc()).offset((page - 1) * size).limit(size)).all()
    return rows, total


def _filter_articles(query, keyword, category_id, author_id, tag):
    if keyword:
        query = query.where(or_(Article.title.contains(keyword), Article.content.contains(keyword),
                                Article.summary.contains(keyword)))
    if category_id:
        query = query.where(Article.category_id == category_id)
    if author_id:
        query = query.where(Article.author_id == author_id)
    if tag:
        query = query.where(Article.tags.any((Tag.name == tag) & Tag.deleted_at.is_(None)))
    return query
