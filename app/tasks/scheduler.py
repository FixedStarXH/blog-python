import asyncio
import logging
from datetime import datetime
from sqlalchemy import select, delete, update
from app.db.session import SessionLocal
from app.models import Article, ArticleChunk
from app.rag.jobs import enqueue_stale_jobs, run_due_jobs
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
        except Exception:
            logger.exception("Scheduled publication failed; due articles will be retried")


async def rag_maintenance_loop():
    while True:
        await asyncio.sleep(30)
        try:
            with SessionLocal() as db:
                hidden_ids = select(Article.id).where(
                    (Article.status != 1) | (Article.password != "") | Article.deleted_at.is_not(None)
                )
                db.execute(delete(ArticleChunk).where(ArticleChunk.article_id.in_(hidden_ids)))
                if not (settings.embedding_base_url and settings.embedding_api_key):
                    db.commit()
                    continue
                enqueue_stale_jobs(db)
            await run_due_jobs()
        except Exception:
            logger.exception("RAG index maintenance failed")
