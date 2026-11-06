"""Lock taxonomy rows while modifying article associations."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models import Category, ColumnModel, Tag


def lock_category(db: Session, category_id: int):
    changed = db.execute(update(Category).where(
        Category.id == category_id, Category.deleted_at.is_(None)
    ).values(sort=Category.sort)).rowcount
    if not changed:
        raise DomainError("category not found", 400)


def lock_column(db: Session, column_id: int | None, author_id: int):
    if column_id is None:
        return
    changed = db.execute(update(ColumnModel).where(
        ColumnModel.id == column_id, ColumnModel.author_id == author_id,
        ColumnModel.deleted_at.is_(None)
    ).values(status=ColumnModel.status)).rowcount
    if not changed:
        raise DomainError("column not found", 400)


def lock_tags(db: Session, tag_ids: list[int]) -> list[Tag]:
    unique_ids = sorted(set(tag_ids))
    for tag_id in unique_ids:
        changed = db.execute(update(Tag).where(
            Tag.id == tag_id, Tag.deleted_at.is_(None)
        ).values(name=Tag.name)).rowcount
        if not changed:
            raise DomainError("one or more tags were not found", 400)
    return list(db.scalars(select(Tag).where(Tag.id.in_(unique_ids))).all()) if unique_ids else []
