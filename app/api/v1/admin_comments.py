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
from app.schemas.common import CommentBatchInput, CommentStatusInput

router = APIRouter(prefix="/admin")

@router.get("/comments")
def comments(status: int | None = None, page: int = 1, pageSize: int = 20, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    q = select(Comment).where(Comment.deleted_at.is_(None))
    if status is not None and status != -1: q = q.where(Comment.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Comment.created_at.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
    article_ids={c.article_id for c in rows}
    titles={a.id:a.title for a in db.scalars(select(Article).where(Article.id.in_(article_ids))).all()} if article_ids else {}
    return envelope({"list":[{"id":c.id,"articleId":c.article_id,"articleTitle":titles.get(c.article_id,""),"nickname":c.nickname,"content":c.content,"status":c.status,"createdAt":c.created_at.isoformat()} for c in rows],"total":total,"page":page,"pageSize":pageSize})

@router.put("/comments/{comment_id}/approve")
def approve_comment(comment_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    c = db.get(Comment, comment_id)
    if not c or c.deleted_at: raise DomainError("comment not found", 404)
    c.status = 1; db.commit(); return envelope({"id":c.id,"status":c.status})

@router.put("/comments/{comment_id}/status")
def set_comment_status(comment_id: int, body: CommentStatusInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    c = db.get(Comment, comment_id)
    if not c or c.deleted_at: raise DomainError("comment not found", 404)
    c.status = body.status; db.commit(); return envelope({"id":c.id,"status":c.status})

@router.put("/comments/{comment_id}/reject")
def reject_comment(comment_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    c = db.get(Comment, comment_id)
    if not c or c.deleted_at: raise DomainError("comment not found", 404)
    c.status = 2; db.commit(); return envelope({"id":c.id,"status":c.status})

@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    c = db.get(Comment, comment_id)
    if not c or c.deleted_at: raise DomainError("comment not found", 404)
    c.deleted_at = datetime.utcnow(); db.commit(); return envelope({})

@router.post("/comments/batch")
def batch_comments(body: CommentBatchInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    rows = db.scalars(select(Comment).where(Comment.id.in_(body.ids), Comment.deleted_at.is_(None))).all()
    for c in rows:
        if body.action == "delete": c.deleted_at=datetime.utcnow()
        else: c.status=1
    db.commit(); return envelope({"affected":len(rows)})
