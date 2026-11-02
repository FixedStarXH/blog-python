from datetime import datetime, timedelta
from pathlib import Path
import secrets
from fastapi import APIRouter, Depends, File, UploadFile, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_admin, require_editor
from app.core.config import settings
from app.core.errors import DomainError, envelope
from app.core.security import hash_password
from app.core.security import hash_password
from app.models import Article, AuditLog, Category, Comment, Image, Link, MetricEvent, SiteSetting, Tag, User
from app.api.v1.articles import article_data
from app.api.v1.articles import _parse_datetime
from app.schemas.common import AdminArticleInput

router = APIRouter(prefix="/admin")

@router.get("/articles")
def articles(status: int | None = None, keyword: str = "", categoryId: int|None=None, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(require_editor)):
    q = select(Article).where(Article.deleted_at.is_(None))
    if status is not None: q = q.where(Article.status == status)
    if keyword: q = q.where(Article.title.contains(keyword))
    if categoryId: q=q.where(Article.category_id==categoryId)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Article.updated_at.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list": [article_data(a) for a in rows], "total": total, "page": page, "pageSize": pageSize})


@router.get("/articles/trash")
def trashed_articles(page: int=Query(1,ge=1),pageSize: int=Query(20,ge=1,le=100),db: Session=Depends(get_db),user: User=Depends(require_editor)):
    q=select(Article).where(Article.deleted_at.is_not(None))
    total=db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows=db.scalars(q.order_by(Article.deleted_at.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[article_data(a) for a in rows],"total":total,"page":page,"pageSize":pageSize})

@router.get("/articles/{article_id}")
def article_detail(article_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    a = db.get(Article, article_id)
    if not a or a.deleted_at: raise DomainError("article not found", 404)
    return envelope(article_data(a))

@router.post("/articles", status_code=201)
def create_article(body: AdminArticleInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    body=body.model_dump(by_alias=True)
    category_id = int(body.get("categoryId", body.get("category_id", 0)))
    if not db.get(Category, category_id): raise DomainError("category not found", 400)
    raw_password=body.get("password","")
    a = Article(author_id=user.id, title=str(body.get("title", "")).strip(), content=str(body.get("content", "")), summary=str(body.get("summary", "")), category_id=category_id, status=int(body.get("status", 1)), cover_image=body.get("coverImage", ""), source_url=body.get("sourceUrl", ""), password=hash_password(raw_password) if raw_password else "", is_top=bool(body.get("isTop", False)), is_featured=bool(body.get("isFeatured", False)),publish_at=_parse_datetime(body.get("publishAt")))
    if not a.title or not a.content: raise DomainError("title and content are required")
    tag_ids=body.get("tagIds",[]); a.tags=db.scalars(select(Tag).where(Tag.id.in_(tag_ids),Tag.deleted_at.is_(None))).all() if tag_ids else []
    if len(a.tags)!=len(set(tag_ids)): raise DomainError("one or more tags were not found",400)
    if a.status==1 and a.publish_at and a.publish_at>datetime.utcnow(): a.status=4
    db.add(a); db.commit(); db.refresh(a); return envelope(article_data(a))

@router.put("/articles/{article_id}")
def update_article(article_id: int, body: AdminArticleInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    body=body.model_dump(by_alias=True)
    a = db.get(Article, article_id)
    if not a or a.deleted_at: raise DomainError("article not found", 404)
    fields = {"title":"title", "content":"content", "summary":"summary", "coverImage":"cover_image", "sourceUrl":"source_url", "password":"password", "categoryId":"category_id", "status":"status", "isTop":"is_top", "isFeatured":"is_featured", "publishAt":"publish_at"}
    for key, attr in fields.items():
        if key in body:
            value=body[key]
            if key=="password": value=hash_password(value) if value else ""
            if key=="publishAt": value=_parse_datetime(value)
            setattr(a, attr, value)
    if "categoryId" in body and not db.get(Category,a.category_id): raise DomainError("category not found",400)
    if "tagIds" in body:
        a.tags=db.scalars(select(Tag).where(Tag.id.in_(body["tagIds"]),Tag.deleted_at.is_(None))).all()
        if len(a.tags)!=len(set(body["tagIds"])): raise DomainError("one or more tags were not found",400)
    if a.status==1 and a.publish_at and a.publish_at>datetime.utcnow(): a.status=4
    a.updated_at = datetime.utcnow(); db.commit(); return envelope(article_data(a))

@router.delete("/articles/{article_id}")
def delete_article(article_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    a = db.get(Article, article_id)
    if not a or a.deleted_at: raise DomainError("article not found", 404)
    a.deleted_at = datetime.utcnow(); db.commit(); return envelope({})


@router.put("/articles/{article_id}/restore")
def restore_article(article_id: int,db: Session=Depends(get_db),user: User=Depends(require_editor)):
    a=db.get(Article,article_id)
    if not a or not a.deleted_at: raise DomainError("trashed article not found",404)
    a.deleted_at=None;a.updated_at=datetime.utcnow();db.commit();return envelope(article_data(a))

@router.put("/articles/{article_id}/approve")
def approve_article(article_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    a = db.get(Article, article_id)
    if not a or a.deleted_at: raise DomainError("article not found", 404)
    if a.status != 2: raise DomainError("only pending articles can be approved", 409)
    a.status = 4 if a.publish_at and a.publish_at>datetime.utcnow() else 1
    if a.status==1 and not a.publish_at: a.publish_at=datetime.utcnow()
    a.reject_reason = ""; db.commit(); return envelope(article_data(a))

@router.put("/articles/{article_id}/reject")
def reject_article(article_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    a = db.get(Article, article_id)
    if not a or a.deleted_at: raise DomainError("article not found", 404)
    if a.status != 2: raise DomainError("only pending articles can be rejected", 409)
    a.status = 3; a.reject_reason = str(body.get("reason", ""))[:255]; db.commit(); return envelope(article_data(a))

@router.post("/articles/batch")
def batch_articles(body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    ids = [int(i) for i in body.get("ids", [])]; op = body.get("action", body.get("operation", ""))
    rows = db.scalars(select(Article).where(Article.id.in_(ids), Article.deleted_at.is_(None))).all()
    for a in rows:
        if op in ("publish", "approve"):
            a.status=4 if a.publish_at and a.publish_at>datetime.utcnow() else 1
            if a.status==1 and not a.publish_at: a.publish_at=datetime.utcnow()
        elif op in ("draft", "setDraft"): a.status = 0
        elif op == "top": a.is_top = True
        elif op == "untop": a.is_top = False
        elif op == "delete": a.deleted_at = datetime.utcnow()
        else: raise DomainError("unsupported batch operation")
    db.commit(); return envelope({"affected": len(rows)})
