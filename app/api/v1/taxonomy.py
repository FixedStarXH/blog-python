from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_editor
from app.core.errors import DomainError, envelope
from app.models import Article, Category, Tag, User

router = APIRouter()


@router.get("/categories")
def categories(db: Session = Depends(get_db)):
    rows = db.scalars(select(Category).where(Category.deleted_at.is_(None)).order_by(Category.sort, Category.id)).all()
    return envelope([{"id": x.id, "name": x.name, "description": x.description, "sort": x.sort, "articleCount": db.scalar(select(func.count(Article.id)).where(Article.category_id == x.id, Article.deleted_at.is_(None))) or 0} for x in rows])


@router.get("/tags")
def tags(db: Session = Depends(get_db)):
    rows = db.scalars(select(Tag).where(Tag.deleted_at.is_(None)).order_by(Tag.name)).all()
    return envelope([{"id": x.id, "name": x.name} for x in rows])


@router.get("/admin/tags")
def admin_tags(db: Session = Depends(get_db), user: User = Depends(require_editor), page: int=1, pageSize: int=20, keyword: str=""):
    q=select(Tag).where(Tag.deleted_at.is_(None))
    if keyword: q=q.where(Tag.name.contains(keyword))
    total=db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows=db.scalars(q.order_by(Tag.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[{"id":x.id,"name":x.name,"articleCount":db.scalar(select(func.count(Article.id)).join(Article.tags).where(Tag.id==x.id,Article.deleted_at.is_(None))) or 0} for x in rows],"total":total,"page":page,"pageSize":pageSize})


@router.post("/admin/categories", status_code=201)
def create_category(body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    name = str(body.get("name", "")).strip()
    if not name: raise DomainError("category name is required")
    if db.scalar(select(Category.id).where(Category.name == name)): raise DomainError("category already exists", 409)
    x = Category(name=name, description=str(body.get("description", "")), sort=int(body.get("sort", 0))); db.add(x); db.commit(); db.refresh(x)
    return envelope({"id": x.id, "name": x.name})


@router.put("/admin/categories/{item_id}")
def update_category(item_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x = db.get(Category, item_id)
    if not x or x.deleted_at: raise DomainError("category not found", 404)
    if "name" in body and body["name"]!=x.name and db.scalar(select(Category.id).where(Category.name==body["name"])): raise DomainError("category already exists",409)
    for key in ("name", "description", "sort"):
        if key in body: setattr(x, key, body[key])
    db.commit(); return envelope({"id": x.id, "name": x.name})


@router.delete("/admin/categories/{item_id}")
def delete_category(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x = db.get(Category, item_id)
    if not x or x.deleted_at: raise DomainError("category not found", 404)
    if db.scalar(select(func.count(Article.id)).where(Article.category_id == item_id, Article.deleted_at.is_(None))): raise DomainError("category is in use", 409)
    from datetime import datetime
    x.deleted_at = datetime.utcnow(); db.commit(); return envelope({})


@router.post("/admin/tags", status_code=201)
def create_tag(body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    name = str(body.get("name", "")).strip()
    if not name: raise DomainError("tag name is required")
    if db.scalar(select(Tag.id).where(Tag.name == name)): raise DomainError("tag already exists", 409)
    x = Tag(name=name); db.add(x); db.commit(); db.refresh(x); return envelope({"id": x.id, "name": x.name})


@router.put("/admin/tags/{item_id}")
def update_tag(item_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x = db.get(Tag, item_id)
    if not x or x.deleted_at: raise DomainError("tag not found", 404)
    name=str(body.get("name",x.name)).strip()
    if name!=x.name and db.scalar(select(Tag.id).where(Tag.name==name)): raise DomainError("tag already exists",409)
    x.name = name; db.commit(); return envelope({"id": x.id, "name": x.name})


@router.delete("/admin/tags/{item_id}")
def delete_tag(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    x = db.get(Tag, item_id)
    if not x or x.deleted_at: raise DomainError("tag not found", 404)
    from datetime import datetime
    for article in x.articles: article.tags.remove(x)
    x.deleted_at = datetime.utcnow(); db.commit(); return envelope({})
