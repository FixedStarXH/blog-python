from datetime import datetime, timedelta
from pathlib import Path
import secrets
from fastapi import APIRouter, Depends, File, UploadFile, Query
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_admin, require_editor
from app.core.config import settings
from app.core.errors import DomainError, envelope
from app.core.security import hash_password
from app.core.security import hash_password
from app.models import Article, AuditLog, Category, Comment, Image, Link, MetricEvent, SiteSetting, Tag, User
from app.api.v1.articles import article_data
from app.api.v1.articles import _parse_datetime
from app.schemas.common import AdminArticleInput, ArticleBatchInput, ArticleRejectInput
from app.services.taxonomy import lock_category, lock_column, lock_tags

router = APIRouter(prefix="/admin")

@router.get("/articles")
def articles(status: int | None = None, keyword: str = "", categoryId: int|None=None, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(require_editor)):
    q = select(Article).where(Article.deleted_at.is_(None))
    if status is not None and status >= 0: q = q.where(Article.status == status)
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
    lock_category(db, category_id)
    lock_column(db, body["columnId"], user.id)
    tags = lock_tags(db, body["tagIds"])
    raw_password=body.get("password","")
    a = Article(author_id=user.id, title=str(body.get("title", "")).strip(), content=str(body.get("content", "")), summary=str(body.get("summary", "")), category_id=category_id, status=int(body.get("status", 1)), cover_image=body.get("coverImage", ""), source_url=body.get("sourceUrl", ""), password=hash_password(raw_password) if raw_password else "", is_top=bool(body.get("isTop", False)), is_featured=bool(body.get("isFeatured", False)),publish_at=_parse_datetime(body.get("publishAt")))
    a.column_id = body["columnId"]
    if not a.title or not a.content: raise DomainError("title and content are required")
    a.tags = tags
    if a.status==1 and a.publish_at and a.publish_at>datetime.utcnow(): a.status=4
    if a.status == 4 and not a.publish_at: raise DomainError("scheduled articles need a publication time", 400)
    db.add(a); db.commit(); db.refresh(a); return envelope(article_data(a))

@router.put("/articles/{article_id}")
def update_article(article_id: int, body: AdminArticleInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    column_supplied = "column_id" in body.model_fields_set
    body=body.model_dump(by_alias=True)
    a = db.get(Article, article_id)
    if not a or a.deleted_at: raise DomainError("article not found", 404)
    lock_category(db, body["categoryId"])
    if column_supplied:
        lock_column(db, body["columnId"], a.author_id)
    tags = lock_tags(db, body["tagIds"])
    if body["clearPassword"] and body["password"]: raise DomainError("choose a new password or clear the existing password")
    fields = {"title":"title", "content":"content", "summary":"summary", "coverImage":"cover_image", "sourceUrl":"source_url", "categoryId":"category_id", "status":"status", "isTop":"is_top", "isFeatured":"is_featured", "publishAt":"publish_at"}
    for key, attr in fields.items():
        if key in body:
            value=body[key]
            if key=="publishAt": value=_parse_datetime(value)
            setattr(a, attr, value)
    if body["clearPassword"]: a.password = ""
    elif body["password"]: a.password = hash_password(body["password"])
    if column_supplied:
        a.column_id = body["columnId"]
    a.tags = tags
    if a.status==1 and a.publish_at and a.publish_at>datetime.utcnow(): a.status=4
    if a.status == 4 and not a.publish_at: raise DomainError("scheduled articles need a publication time", 400)
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
    now = datetime.utcnow()
    changed = db.execute(update(Article).where(
        Article.id == article_id, Article.deleted_at.is_(None), Article.status == 2
    ).values(
        status=case((Article.publish_at > now, 4), else_=1),
        publish_at=func.coalesce(Article.publish_at, now),
        reject_reason="", updated_at=now,
    )).rowcount
    if not changed:
        db.rollback()
        if not db.scalar(select(Article.id).where(Article.id == article_id, Article.deleted_at.is_(None))):
            raise DomainError("article not found", 404)
        raise DomainError("only pending articles can be approved", 409)
    db.commit()
    return envelope(article_data(db.get(Article, article_id)))

@router.put("/articles/{article_id}/reject")
def reject_article(article_id: int, body: ArticleRejectInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    changed = db.execute(update(Article).where(
        Article.id == article_id, Article.deleted_at.is_(None), Article.status == 2
    ).values(status=3, reject_reason=body.reason, updated_at=datetime.utcnow())).rowcount
    if not changed:
        db.rollback()
        if not db.scalar(select(Article.id).where(Article.id == article_id, Article.deleted_at.is_(None))):
            raise DomainError("article not found", 404)
        raise DomainError("only pending articles can be rejected", 409)
    db.commit()
    return envelope(article_data(db.get(Article, article_id)))

@router.post("/articles/batch")
def batch_articles(body: ArticleBatchInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    ids = body.ids
    op = body.action or body.operation
    if not op: raise DomainError("batch action is required", 400)
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
