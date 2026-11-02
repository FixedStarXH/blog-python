import asyncio
import logging
from datetime import datetime
from sqlalchemy import func, select, delete
from app.db.session import SessionLocal
from app.models import Article, ArticleChunk
from app.rag.indexing import index_article
from app.core.config import settings

logger=logging.getLogger(__name__)


async def publishing_loop():
    while True:
        await asyncio.sleep(20)
        with SessionLocal() as db:
            due=db.scalars(select(Article).where(Article.status==4,Article.publish_at<=datetime.utcnow(),Article.deleted_at.is_(None))).all()
            for article in due: article.status=1
            if due: db.commit()
            if due and settings.embedding_base_url and settings.embedding_api_key:
                for article in due:
                    try: await index_article(db,article)
                    except Exception: logger.exception("scheduled article indexing failed article_id=%s",article.id)


async def rag_maintenance_loop():
    while True:
        await asyncio.sleep(30)
        with SessionLocal() as db:
            db.execute(delete(ArticleChunk).where(ArticleChunk.article_id.in_(select(Article.id).where((Article.status!=1)|(Article.password!="")|Article.deleted_at.is_not(None)))))
            db.commit()
            if not (settings.embedding_base_url and settings.embedding_api_key): continue
            articles=db.scalars(select(Article).where(Article.status==1,Article.password=="",Article.deleted_at.is_(None))).all()
            for article in articles:
                last_index=db.scalar(select(func.max(ArticleChunk.indexed_at)).where(ArticleChunk.article_id==article.id))
                if last_index is None or article.updated_at>last_index:
                    try: await index_article(db,article)
                    except Exception: logger.exception("RAG refresh failed article_id=%s",article.id)
