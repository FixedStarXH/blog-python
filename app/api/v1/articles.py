from datetime import datetime, timezone
import hashlib
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db, optional_user, require_editor
from app.core.errors import DomainError, envelope
from app.core.security import hash_password, verify_password
from app.models import Article, Category, ColumnModel, MetricEvent, Tag, User
from app.repositories.articles import page_public, visible_conditions
from app.schemas.common import ArticleInput

router = APIRouter()


def article_data(a: Article, content=True):
    result = {"id": a.id, "title": a.title, "summary": a.summary, "coverImage": a.cover_image, "sourceUrl": a.source_url, "authorId": a.author_id, "author": {"id": a.author.id, "username": a.author.username, "nickname": a.author.nickname} if a.author else None, "categoryId": a.category_id, "category": {"id": a.category.id, "name": a.category.name} if a.category and not a.category.deleted_at else None, "tags": [{"id": t.id, "name": t.name} for t in a.tags if not t.deleted_at], "status": a.status, "viewCount": a.view_count, "likeCount": a.like_count, "isTop": a.is_top, "isFeatured": a.is_featured, "publishAt": a.publish_at.isoformat() if a.publish_at else None, "createdAt": a.created_at.isoformat()}
    if content:
        result["content"] = a.content
        result["rejectReason"] = a.reject_reason
        result["password"] = ""  # Never expose a stored private-article password hash.
    return result


@router.get("/articles")
def public_articles(db: Session = Depends(get_db), keyword: str = "", q: str = "", tag: str = "", featured: bool = False, seed: int=1, categoryId: int | None = None, authorId: int | None = None, sort: str = "latest", page: int = Query(1, ge=1), pageSize: int = Query(10, ge=1, le=50)):
    rows, total = page_public(db, page, pageSize, keyword or q, categoryId, authorId, sort, tag, featured, seed)
    return envelope({"list": [article_data(a, False) for a in rows], "total": total, "page": page, "pageSize": pageSize})


@router.get("/articles/hot")
def hot_articles(db: Session = Depends(get_db), limit: int = Query(10, ge=1, le=50)):
    rows, _ = page_public(db, 1, limit, sort="hot")
    return envelope([article_data(a, False) for a in rows])


@router.get("/articles/{article_id}")
def public_article(article_id: int, db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions()))
    if not a:
        raise DomainError("article not found", 404)
    if a.password:
        return envelope({"id": a.id, "title": a.title, "needPassword": True})
    return envelope(article_data(a))


@router.post("/articles/{article_id}/unlock")
def unlock_article(article_id: int, body: dict, db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions()))
    if not a or not a.password or not verify_password(str(body.get("password", "")), a.password):
        raise DomainError("article password is incorrect", 403)
    return envelope(article_data(a))


@router.get("/articles/{article_id}/nav")
def article_nav(article_id: int, db: Session = Depends(get_db)):
    current=db.scalar(select(Article).where(Article.id==article_id,*visible_conditions()))
    if not current: raise DomainError("article not found",404)
    previous=db.scalar(select(Article).where(*visible_conditions(),Article.password=="",Article.id<article_id).order_by(Article.id.desc()).limit(1))
    following=db.scalar(select(Article).where(*visible_conditions(),Article.password=="",Article.id>article_id).order_by(Article.id).limit(1))
    shape=lambda a:{"id":a.id,"title":a.title} if a else None
    related=db.scalars(select(Article).where(*visible_conditions(),Article.password=="",Article.category_id==current.category_id,Article.id!=current.id).order_by(Article.view_count.desc()).limit(5)).all()
    return envelope({"prev":shape(previous),"next":shape(following),"related":[{"id":a.id,"title":a.title,"summary":a.summary,"coverImage":a.cover_image} for a in related]})


@router.put("/articles/{article_id}/view")
def add_view(article_id: int, request: Request, db: Session = Depends(get_db),user: User|None=Depends(optional_user)):
    a = db.get(Article, article_id)
    if not a or a.status != 1 or a.deleted_at or (a.publish_at and a.publish_at > datetime.utcnow()):
        raise DomainError("article not found", 404)
    raw=f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    fingerprint=hashlib.sha256(raw.encode()).hexdigest()
    try:
        db.add(MetricEvent(article_id=article_id, kind="view", fingerprint=fingerprint))
        db.flush()
        db.execute(update(Article).where(Article.id==article_id).values(view_count=Article.view_count+1))
        db.commit();db.refresh(a)
    except IntegrityError:
        db.rollback();a=db.get(Article,article_id)
    return envelope({"viewCount": a.view_count})


@router.put("/articles/{article_id}/like")
def like_article(article_id: int, request: Request, db: Session = Depends(get_db),user: User|None=Depends(optional_user)):
    a = db.get(Article, article_id)
    if not a or a.status != 1 or a.deleted_at or (a.publish_at and a.publish_at > datetime.utcnow()):
        raise DomainError("article not found", 404)
    raw=f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    fingerprint=hashlib.sha256(raw.encode()).hexdigest()
    try:
        db.add(MetricEvent(article_id=article_id, kind="like", fingerprint=fingerprint));db.flush()
        db.execute(update(Article).where(Article.id==article_id).values(like_count=Article.like_count+1));db.commit();db.refresh(a)
    except IntegrityError:
        db.rollback();a=db.get(Article,article_id)
    return envelope({"likeCount": a.like_count})


@router.get("/articles/{article_id}/export")
def export_article(article_id: int, db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, *visible_conditions(), Article.password == ""))
    if not a:
        raise DomainError("article not found", 404)
    import re, html
    content=html.unescape(re.sub(r"</(p|h[1-6]|li|blockquote|pre)>\s*", "\n\n", a.content, flags=re.I))
    content=re.sub(r"<br\s*/?>","\n",content,flags=re.I)
    content=re.sub(r"<[^>]+>","",content).strip()
    return envelope({"title":a.title,"content":content,"format":"md"})


@router.get("/my/articles")
def my_articles(user: User = Depends(current_user), db: Session = Depends(get_db), page: int = Query(1, ge=1), pageSize: int = Query(10, ge=1, le=50), status: int | None = None):
    query = select(Article).where(Article.author_id == user.id, Article.deleted_at.is_(None))
    if status is not None:
        query = query.where(Article.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Article.updated_at.desc()).offset((page - 1) * pageSize).limit(pageSize)).all()
    return envelope({"list": [article_data(a) for a in rows], "total": total, "page": page, "pageSize": pageSize})


@router.post("/my/articles", status_code=201)
def create_my_article(body: ArticleInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(Category,body.category_id): raise DomainError("category not found",400)
    if body.column_id and not db.scalar(select(ColumnModel.id).where(ColumnModel.id==body.column_id,ColumnModel.author_id==user.id,ColumnModel.deleted_at.is_(None))): raise DomainError("column not found",400)
    a = Article(author_id=user.id, title=body.title, content=body.content, summary=body.summary, cover_image=body.cover_image, source_url=body.source_url, password=hash_password(body.password) if body.password else "", category_id=body.category_id, column_id=body.column_id, status=2, publish_at=_parse_datetime(body.publish_at))
    a.tags = db.scalars(select(Tag).where(Tag.id.in_(body.tag_ids), Tag.deleted_at.is_(None))).all() if body.tag_ids else []
    if len(a.tags)!=len(set(body.tag_ids)): raise DomainError("one or more tags were not found",400)
    db.add(a); db.commit(); db.refresh(a)
    return envelope(article_data(a))


@router.get("/my/articles/{article_id}")
def get_my_article(article_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, Article.author_id == user.id, Article.deleted_at.is_(None)))
    if not a: raise DomainError("article not found", 404)
    return envelope(article_data(a))


@router.put("/my/articles/{article_id}")
def update_my_article(article_id: int, body: ArticleInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, Article.author_id == user.id, Article.deleted_at.is_(None)))
    if not a: raise DomainError("article not found", 404)
    if not db.get(Category,body.category_id): raise DomainError("category not found",400)
    if body.column_id and not db.scalar(select(ColumnModel.id).where(ColumnModel.id==body.column_id,ColumnModel.author_id==user.id,ColumnModel.deleted_at.is_(None))): raise DomainError("column not found",400)
    for attr, value in (("title", body.title), ("content", body.content), ("summary", body.summary), ("cover_image", body.cover_image), ("source_url", body.source_url), ("password", hash_password(body.password) if body.password else ""), ("category_id", body.category_id), ("column_id", body.column_id)):
        setattr(a, attr, value)
    a.publish_at=_parse_datetime(body.publish_at)
    a.tags = db.scalars(select(Tag).where(Tag.id.in_(body.tag_ids), Tag.deleted_at.is_(None))).all() if body.tag_ids else []
    if len(a.tags)!=len(set(body.tag_ids)): raise DomainError("one or more tags were not found",400)
    if a.status in (0,3): a.status = 2
    a.updated_at = datetime.utcnow(); db.commit()
    return envelope(article_data(a))


@router.delete("/my/articles/{article_id}")
def delete_my_article(article_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.scalar(select(Article).where(Article.id == article_id, Article.author_id == user.id, Article.deleted_at.is_(None)))
    if not a: raise DomainError("article not found", 404)
    a.deleted_at = datetime.utcnow(); db.commit()
    return envelope({})


def _parse_datetime(value):
    if not value: return None
    try:
        parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError,ValueError) as exc:
        raise DomainError("invalid publishAt timestamp") from exc
