"""Durable view outbox with best-effort Redis wakeups."""
import logging
from collections import Counter

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models import Article, MetricEvent

logger = logging.getLogger(__name__)
PENDING_KEY = "blog:views:pending"


async def notify_view(redis, event_id: int) -> None:
    if redis is None:
        return
    try:
        await redis.sadd(PENDING_KEY, event_id)
    except Exception:
        # The DB outbox is authoritative; the periodic sweep will recover it.
        logger.warning("Redis view notification failed; database sweep will retry")


def flush_pending_views(event_ids=None, limit: int = 500) -> int:
    """Claim and apply a bounded batch in one transaction; safe to retry."""
    with SessionLocal() as db:
        query = select(MetricEvent.id, MetricEvent.article_id).where(
            MetricEvent.kind == "view", MetricEvent.counted.is_(False)
        ).order_by(MetricEvent.id).limit(limit)
        if event_ids is not None:
            if not event_ids:
                return 0
            query = query.where(MetricEvent.id.in_(event_ids))
        rows = db.execute(query).all()
        counts = Counter()
        for event_id, article_id in rows:
            claimed = db.execute(update(MetricEvent).where(
                MetricEvent.id == event_id, MetricEvent.kind == "view", MetricEvent.counted.is_(False)
            ).values(counted=True)).rowcount
            if claimed:
                counts[article_id] += 1
        for article_id, count in sorted(counts.items()):
            db.execute(update(Article).where(Article.id == article_id).values(
                view_count=Article.view_count + count, updated_at=Article.updated_at
            ))
        db.commit()
        return sum(counts.values())
