from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_editor
from app.core.errors import DomainError, envelope
from app.models import Article, ArticleChunk, User
from app.rag.indexing import index_article

router=APIRouter(prefix="/admin/ai")


@router.post("/index/{article_id}")
async def index_one(article_id: int, db: Session=Depends(get_db), user: User=Depends(require_editor)):
    article=db.get(Article,article_id)
    if not article or article.deleted_at: raise DomainError("article not found",404)
    return envelope({"indexed":await index_article(db,article)})


@router.post("/index-all")
async def index_all(db: Session=Depends(get_db), user: User=Depends(require_editor)):
    articles=db.scalars(select(Article).where(Article.status==1,Article.password=="",Article.deleted_at.is_(None))).all()
    result=[]
    for article in articles:
        result.append({"articleId":article.id,"chunks":await index_article(db,article)})
    return envelope({"articles":len(result),"indexed":sum(x["chunks"] for x in result)})


@router.get("/index-status")
def index_status(db: Session=Depends(get_db), user: User=Depends(require_editor)):
    count=db.scalar(select(func.count(ArticleChunk.id))) or 0
    return envelope({"chunks":count,"indexedArticles":db.scalar(select(func.count(func.distinct(ArticleChunk.article_id)))) or 0})
