import asyncio
import logging
from datetime import datetime
from sqlalchemy import func, select, delete, update
from app.db.session import SessionLocal
from app.models import Article, ArticleChunk
from app.rag.indexing import index_article
from app.core.config import settings
from app.services.view_counts import PENDING_KEY, flush_pending_views

logger=logging.getLogger(__name__)


async def view_flush_loop(app):
    while True:
        await asyncio.sleep(3)
        redis = getattr(app.state, "redis", None)
        event_ids = []
        if redis:
            try:
                event_ids = [int(value) for value in await redis.spop(PENDING_KEY, 500)]
            except Exception:
                logger.debug("Redis view queue unavailable; using database sweep", exc_info=True)
        try:
            notified = await asyncio.to_thread(flush_pending_views, event_ids) if event_ids else 0
            # Always sweep: Redis signals can be lost on a crash or outage.
            recovered = await asyncio.to_thread(flush_pending_views)
            if (notified or recovered) and redis:
                try:
                    await redis.incr("blog:http:version")
                except Exception:
                    logger.debug("Cache invalidation unavailable after view flush", exc_info=True)
        except Exception:
            logger.exception("View count flush failed; pending events will be retried")


def publish_due_articles(db, now):
    ids = db.scalars(select(Article.id).where(
        Article.status == 4, Article.publish_at <= now, Article.deleted_at.is_(None)
    )).all()
    published = []
    for article_id in ids:
        changed = db.execute(update(Article).where(
            Article.id == article_id, Article.status == 4,
            Article.publish_at <= now, Article.deleted_at.is_(None)
        ).values(status=1, updated_at=now)).rowcount
        if changed:
            published.append(article_id)
    db.commit()
    return published


async def publishing_loop(app):
    while True:
        await asyncio.sleep(20)
        try:
            with SessionLocal() as db:
                published = publish_due_articles(db, datetime.utcnow())
                redis = getattr(app.state, "redis", None)
                if published and redis:
                    try:
                        await redis.incr("blog:http:version")
                    except Exception:
                        logger.warning("Cache invalidation unavailable after scheduled publication", exc_info=True)
                if published and settings.embedding_base_url and settings.embedding_api_key:
                    for article_id in published:
                        article = db.get(Article, article_id)
                        if article:
                            try: await index_article(db, article)
                            except Exception: logger.exception("scheduled article indexing failed article_id=%s", article_id)
        except Exception:
            logger.exception("Scheduled publication failed; due articles will be retried")


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
                stale_model=db.scalar(select(ArticleChunk.id).where(ArticleChunk.article_id==article.id,ArticleChunk.embedding_model!=settings.embedding_model).limit(1))
                stale_dimension = settings.embedding_dimension and db.scalar(select(ArticleChunk.id).where(ArticleChunk.article_id==article.id,ArticleChunk.embedding_dim!=settings.embedding_dimension).limit(1))
                if last_index is None or article.updated_at>last_index or stale_model or stale_dimension:
                    try: await index_article(db,article)
                    except Exception: logger.exception("RAG refresh failed article_id=%s",article.id)
