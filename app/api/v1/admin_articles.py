from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_admin, require_editor
from app.core.config import settings
from app.core.errors import DomainError, envelope
from app.models import Article, AuditLog, Category, Comment, Image, Link, MetricEvent, SiteSetting, Tag, User
from app.api.v1.articles import article_data
from app.schemas.common import AdminArticleInput, ArticleBatchInput, ArticleRejectInput
from app.services import admin_articles as article_service

router = APIRouter(prefix="/admin")

@router.get("/articles")
def articles(status: int | None = None, keyword: str = "", categoryId: int|None=None, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(require_editor)):
    rows, total = article_service.list_articles(db, status, keyword, categoryId, page, pageSize)
    return envelope({"list": [article_data(a) for a in rows], "total": total, "page": page, "pageSize": pageSize})


@router.get("/articles/trash")
def trashed_articles(page: int=Query(1,ge=1),pageSize: int=Query(20,ge=1,le=100),db: Session=Depends(get_db),user: User=Depends(require_editor)):
    rows, total = article_service.list_articles(db, None, "", None, page, pageSize, trashed=True)
    return envelope({"list":[article_data(a) for a in rows],"total":total,"page":page,"pageSize":pageSize})

@router.get("/articles/{article_id}")
def article_detail(article_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    a = article_service.get_article(db, article_id)
    return envelope(article_data(a))

@router.post("/articles", status_code=201)
def create_article(body: AdminArticleInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    a = article_service.create_article(db, body, user.id)
    return envelope(article_data(a))

@router.put("/articles/{article_id}")
def update_article(article_id: int, body: AdminArticleInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    a = article_service.update_article(db, article_id, body)
    return envelope(article_data(a))

@router.delete("/articles/{article_id}")
def delete_article(article_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    article_service.delete_article(db, article_id)
    return envelope({})


@router.put("/articles/{article_id}/restore")
def restore_article(article_id: int,db: Session=Depends(get_db),user: User=Depends(require_editor)):
    a=article_service.restore_article(db, article_id)
    return envelope(article_data(a))

@router.put("/articles/{article_id}/approve")
def approve_article(article_id: int, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    article = article_service.moderate_article(db, article_id, approve=True)
    return envelope(article_data(article))

@router.put("/articles/{article_id}/reject")
def reject_article(article_id: int, body: ArticleRejectInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    article = article_service.moderate_article(db, article_id, approve=False, reason=body.reason)
    return envelope(article_data(article))

@router.post("/articles/batch")
def batch_articles(body: ArticleBatchInput, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    affected = article_service.batch_articles(db, body.ids, body.action or body.operation)
    return envelope({"affected": affected})
