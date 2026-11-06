from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_editor
from app.core.errors import DomainError, envelope
from app.models import Article, Category, Tag, User, article_tags
from app.repositories.articles import visible_conditions
from app.schemas.common import CategoryInput, CategoryUpdate, TagInput, TagUpdate
from app.services.taxonomy import lock_category

router = APIRouter()


@router.get("/categories")
def categories(db: Session = Depends(get_db)):
    rows = db.scalars(select(Category).where(Category.deleted_at.is_(None)).order_by(Category.sort, Category.id)).all()
    return envelope([{"id": x.id, "name": x.name, "description": x.description, "sort": x.sort, "articleCount": db.scalar(select(func.count(Article.id)).where(Article.category_id == x.id, *visible_conditions(), Article.password == "")) or 0} for x in rows])


@router.get("/tags")
def tags(db: Session = Depends(get_db)):
    rows = db.scalars(select(Tag).where(Tag.deleted_at.is_(None)).order_by(Tag.name)).all()
    counts = dict(db.execute(select(article_tags.c.tag_id, func.count(Article.id)).join(
        Article, Article.id == article_tags.c.article_id
    ).where(*visible_conditions(), Article.password == "").group_by(article_tags.c.tag_id)).all())
    return envelope([{"id": x.id, "name": x.name, "articleCount": counts.get(x.id, 0)} for x in rows])


@router.get("/admin/tags")
def admin_tags(db: Session = Depends(get_db), user: User = Depends(require_editor), page: int=Query(1, ge=1), pageSize: int=Query(20, ge=1, le=100), keyword: str=""):
    q=select(Tag).where(Tag.deleted_at.is_(None))
    if keyword: q=q.where(Tag.name.contains(keyword))
    total=db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows=db.scalars(q.order_by(Tag.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[{"id":x.id,"name":x.name,"articleCount":db.scalar(select(func.count(Article.id)).join(Article.tags).where(Tag.id==x.id,Article.deleted_at.is_(None))) or 0} for x in rows],"total":total,"page":page,"pageSize":pageSize})


@router.post("/admin/categories", status_code=201)
def create_category(body: CategoryInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    name = body.name
    if db.scalar(select(Category.id).where(Category.name == name)): raise DomainError("category already exists", 409)
    x = Category(name=name, description=body.description, sort=body.sort)
    db.add(x); _commit_unique(db, Category, name); db.refresh(x)
    return envelope({"id": x.id, "name": x.name})


@router.put("/admin/categories/{item_id}")
def update_category(item_id: int, body: CategoryUpdate, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x = db.get(Category, item_id)
    if not x or x.deleted_at: raise DomainError("category not found", 404)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    name = changes.get("name", x.name)
    if name != x.name and db.scalar(select(Category.id).where(Category.name == name)): raise DomainError("category already exists", 409)
    for key, value in changes.items(): setattr(x, key, value)
    _commit_unique(db, Category, name, item_id); return envelope({"id": x.id, "name": x.name})


@router.delete("/admin/categories/{item_id}")
def delete_category(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    try:
        lock_category(db, item_id)
    except DomainError as exc:
        raise DomainError("category not found", 404) from exc
    if db.scalar(select(Article.id).where(Article.category_id == item_id).limit(1).with_for_update()):
        raise DomainError("category is in use, including by trashed articles", 409)
    db.execute(update(Category).where(Category.id == item_id).values(deleted_at=datetime.utcnow()))
    db.commit(); return envelope({})


@router.post("/admin/tags", status_code=201)
def create_tag(body: TagInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    name = body.name
    if db.scalar(select(Tag.id).where(Tag.name == name)): raise DomainError("tag already exists", 409)
    x = Tag(name=name); db.add(x); _commit_unique(db, Tag, name); db.refresh(x); return envelope({"id": x.id, "name": x.name})


@router.put("/admin/tags/{item_id}")
def update_tag(item_id: int, body: TagUpdate, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x = db.get(Tag, item_id)
    if not x or x.deleted_at: raise DomainError("tag not found", 404)
    name = body.name if body.name is not None else x.name
    if name!=x.name and db.scalar(select(Tag.id).where(Tag.name==name)): raise DomainError("tag already exists",409)
    x.name = name; _commit_unique(db, Tag, name, item_id); return envelope({"id": x.id, "name": x.name})


@router.delete("/admin/tags/{item_id}")
def delete_tag(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    changed = db.execute(update(Tag).where(Tag.id == item_id, Tag.deleted_at.is_(None)).values(name=Tag.name)).rowcount
    if not changed: raise DomainError("tag not found", 404)
    db.execute(delete(article_tags).where(article_tags.c.tag_id == item_id))
    db.execute(update(Tag).where(Tag.id == item_id).values(deleted_at=datetime.utcnow()))
    db.commit(); return envelope({})


def _commit_unique(db: Session, model, name: str, exclude_id: int | None = None):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        query = select(model.id).where(model.name == name)
        if exclude_id is not None:
            query = query.where(model.id != exclude_id)
        if db.scalar(query):
            raise DomainError("name already exists", 409) from exc
        raise
