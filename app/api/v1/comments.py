from datetime import datetime
import hashlib
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db, optional_user
from app.core.errors import DomainError, envelope
from app.core.sensitive import filter_sensitive
from app.models import Article, Comment, MetricEvent, User
from app.repositories.articles import visible_conditions
from app.schemas.common import CommentInput

router = APIRouter()


def comment_dict(c):
    return {"id": c.id, "articleId": c.article_id, "userId": c.user_id, "nickname": c.nickname, "content": c.content, "parentId": c.parent_id, "status": c.status, "likeCount": c.like_count, "createdAt": c.created_at.isoformat()}


@router.get("/articles/{article_id}/comments")
def list_comments(article_id: int, db: Session = Depends(get_db), page: int = 1, pageSize: int = 50):
    if not db.scalar(select(Article.id).where(Article.id == article_id, *visible_conditions())):
        raise DomainError("article not found", 404)
    q=select(Comment).where(Comment.article_id == article_id, Comment.status == 1, Comment.deleted_at.is_(None))
    total=db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Comment.created_at).offset((page-1)*pageSize).limit(pageSize)).all()
    return envelope({"list":[comment_dict(c) for c in rows],"total":total,"page":page,"pageSize":pageSize})


@router.post("/articles/{article_id}/comments", status_code=201)
def create_comment(article_id: int, body: CommentInput, request: Request, db: Session = Depends(get_db), user: User | None = Depends(optional_user)):
    article = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions()))
    if not article: raise DomainError("article not found", 404)
    parent_id = body.parent_id
    if parent_id and not db.scalar(select(Comment.id).where(Comment.id == parent_id, Comment.article_id == article_id, Comment.status == 1, Comment.deleted_at.is_(None))):
        raise DomainError("parent comment not found", 404)
    nickname = body.nickname or (user.nickname or user.username if user else "游客")
    c = Comment(article_id=article_id, user_id=user.id if user else None, nickname=filter_sensitive(nickname or "游客"), content=filter_sensitive(body.content), parent_id=parent_id, status=0)
    db.add(c); db.commit(); db.refresh(c); return envelope(comment_dict(c), "comment submitted for review", 201)


@router.put("/articles/{article_id}/comments/{comment_id}/like")
def like_comment(article_id: int, comment_id: int,request: Request, db: Session = Depends(get_db)):
    if not db.scalar(select(Article.id).where(Article.id == article_id, *visible_conditions())):
        raise DomainError("article not found", 404)
    c = db.scalar(select(Comment).where(Comment.id == comment_id, Comment.article_id == article_id, Comment.status == 1, Comment.deleted_at.is_(None)))
    if not c: raise DomainError("comment not found", 404)
    ip=request.client.host if request.client else "unknown"
    fingerprint=hashlib.sha256(f"comment:{comment_id}:ip:{ip}".encode()).hexdigest()
    try:
        db.add(MetricEvent(article_id=article_id,kind="c_like",fingerprint=fingerprint));db.flush()
        db.execute(update(Comment).where(Comment.id==comment_id).values(like_count=Comment.like_count+1));db.commit();db.refresh(c)
    except IntegrityError:
        db.rollback();c=db.get(Comment,comment_id)
    return envelope({"likeCount":c.like_count})
