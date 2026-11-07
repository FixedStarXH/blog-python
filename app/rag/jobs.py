"""Persistent, coalesced indexing jobs with a lease for multi-instance workers."""
import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations.ai.client import EmbeddingUnsupported, embed
from app.models import Article, ArticleChunk, ArticleIndexJob
from app.rag.chunking import split_chunks

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
LEASE_SECONDS = 120
JOB_BATCH_SIZE = 10


def eligible(article: Article, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    return (article.status == 1 and not article.password and article.deleted_at is None
            and (article.publish_at is None or article.publish_at <= now))


def fingerprint(article: Article, model: str | None = None, dimension: int | None = None) -> str:
    value = "\0".join((article.content or "", model if model is not None else settings.embedding_model,
                       str(dimension if dimension is not None else settings.embedding_dimension)))
    return hashlib.sha256(value.encode()).hexdigest()


def enqueue_article(db: Session, article: Article, *, force: bool = False) -> ArticleIndexJob | None:
    """Coalesce pending work so only the most recent eligible article is indexed."""
    if not eligible(article):
        db.execute(delete(ArticleChunk).where(ArticleChunk.article_id == article.id))
        job = db.get(ArticleIndexJob, article.id)
        if job:
            db.delete(job)
        db.flush()
        return None
    digest = fingerprint(article)
    job = db.get(ArticleIndexJob, article.id)
    # The current MySQL DATETIME schema stores seconds. Truncating here avoids
    # rounding a just-enqueued job into the future by up to one second.
    now = datetime.utcnow().replace(microsecond=0)
    actual_chunks = db.scalar(select(func.count(ArticleChunk.id)).where(ArticleChunk.article_id == article.id)) or 0
    index_missing = job is not None and job.status == "complete" and actual_chunks != job.chunk_count
    if job is None:
        job = ArticleIndexJob(article_id=article.id, content_hash=digest,
                              embedding_model=settings.embedding_model, status="pending",
                              attempts=0, next_attempt_at=now, last_error="")
        db.add(job)
    elif force or job.content_hash != digest or job.embedding_model != settings.embedding_model or index_missing:
        job.content_hash = digest
        job.embedding_model = settings.embedding_model
        job.status = "pending"
        job.attempts = 0
        job.next_attempt_at = now
        job.lease_until = None
        job.last_error = ""
        if index_missing:
            job.chunk_count = 0
    return job


def enqueue_stale_jobs(db: Session) -> int:
    """Scan source-of-truth articles; safe to rerun after a crash or missed signal."""
    count = 0
    for article in db.scalars(select(Article)).all():
        before = db.get(ArticleIndexJob, article.id)
        actual_chunks = db.scalar(select(func.count(ArticleChunk.id)).where(ArticleChunk.article_id == article.id)) or 0
        missing_index = before is not None and before.status == "complete" and actual_chunks != before.chunk_count
        was_new_or_stale = (before is None or before.content_hash != fingerprint(article)
                            or before.embedding_model != settings.embedding_model or missing_index)
        job = enqueue_article(db, article)
        if job and was_new_or_stale:
            count += 1
    db.commit()
    return count


def claim_due_jobs(db: Session, limit: int = JOB_BATCH_SIZE, article_id: int | None = None) -> list[int]:
    now = datetime.utcnow().replace(microsecond=0)
    claimable = or_(
        and_(ArticleIndexJob.status.in_(("pending", "retry")), ArticleIndexJob.next_attempt_at <= now),
        and_(ArticleIndexJob.status == "processing", ArticleIndexJob.lease_until <= now),
    )
    query = select(ArticleIndexJob.article_id).where(
        claimable, ArticleIndexJob.attempts < MAX_ATTEMPTS
    ).order_by(ArticleIndexJob.next_attempt_at, ArticleIndexJob.article_id).limit(limit)
    if article_id is not None:
        query = query.where(ArticleIndexJob.article_id == article_id)
    ids = list(db.scalars(query).all())
    claimed = []
    for job_id in ids:
        result = db.execute(update(ArticleIndexJob).where(
            ArticleIndexJob.article_id == job_id,
            claimable,
            ArticleIndexJob.attempts < MAX_ATTEMPTS,
        ).values(status="processing", attempts=ArticleIndexJob.attempts + 1,
                 lease_until=now + timedelta(seconds=LEASE_SECONDS), updated_at=now))
        if result.rowcount:
            claimed.append(job_id)
    # A worker that crashed on its last attempt is terminal after its lease.
    db.execute(update(ArticleIndexJob).where(
        ArticleIndexJob.status == "processing", ArticleIndexJob.lease_until <= now,
        ArticleIndexJob.attempts >= MAX_ATTEMPTS,
    ).values(status="failed", lease_until=None, last_error="worker lease expired after maximum attempts",
             updated_at=now))
    db.commit()
    return claimed


def _safe_error(exc: Exception) -> str:
    message = getattr(exc, "message", "")
    lowered = message.lower()
    if "not configured" in lowered:
        category = "configuration"
    elif "http 401" in lowered or "http 403" in lowered:
        category = "authentication"
    elif "http 429" in lowered:
        category = "rate_limit"
    elif any(word in lowered for word in ("invalid embedding", "dimension mismatch", "finite numbers")):
        category = "invalid_response"
    elif "unavailable" in lowered or "timeout" in type(exc).__name__.lower():
        category = "network"
    elif "http " in lowered:
        category = "upstream"
    else:
        category = "worker"
    detail = message or type(exc).__name__
    return f"{category}: {detail}"[:500]


async def process_index_job(article_id: int) -> int:
    """Embed outside the transaction, then atomically swap only if source is unchanged."""
    with SessionLocal() as db:
        job = db.get(ArticleIndexJob, article_id)
        article = db.get(Article, article_id)
        if not job or job.status != "processing" or not article:
            return 0
        expected_hash = job.content_hash
        expected_model = job.embedding_model
        if not eligible(article):
            db.execute(delete(ArticleChunk).where(ArticleChunk.article_id == article_id))
            db.delete(job)
            db.commit()
            return 0
        content = article.content

    try:
        chunks = split_chunks(content)
        if chunks:
            try:
                vectors = await embed(chunks)
                stored_model = expected_model
            except EmbeddingUnsupported:
                # This is the only provider condition that permits full-text mode.
                vectors = [None] * len(chunks)
                stored_model = "fulltext"
        else:
            vectors, stored_model = [], expected_model

        rows = [ArticleChunk(
            article_id=article_id, chunk_index=index, content=chunk,
            embedding=json.dumps(vector).encode() if vector is not None else None,
            embedding_model=stored_model, embedding_dim=len(vector) if vector is not None else 0,
            content_hash=hashlib.sha256(chunk.encode()).hexdigest(), indexed_at=datetime.utcnow(),
        ) for index, (chunk, vector) in enumerate(zip(chunks, vectors))]

        with SessionLocal() as db:
            job = db.get(ArticleIndexJob, article_id)
            article = db.get(Article, article_id)
            if not job or not article:
                return 0
            if not eligible(article):
                db.execute(delete(ArticleChunk).where(ArticleChunk.article_id == article_id))
                db.delete(job)
                db.commit()
                return 0
            current_hash = fingerprint(article)
            if current_hash != expected_hash or job.content_hash != expected_hash:
                job.content_hash = current_hash
                job.embedding_model = settings.embedding_model
                job.status = "pending"
                job.attempts = 0
                job.next_attempt_at = datetime.utcnow().replace(microsecond=0)
                job.lease_until = None
                job.last_error = ""
                db.commit()
                return 0
            db.execute(delete(ArticleChunk).where(ArticleChunk.article_id == article_id))
            db.add_all(rows)
            job.status = "complete"
            job.chunk_count = len(rows)
            job.lease_until = None
            job.last_error = ""
            job.updated_at = datetime.utcnow()
            db.commit()
        return len(rows)
    except Exception as exc:
        logger.warning("article indexing failed article_id=%s error=%s", article_id, _safe_error(exc))
        with SessionLocal() as db:
            job = db.get(ArticleIndexJob, article_id)
            if job and job.status == "processing":
                now = datetime.utcnow()
                error = _safe_error(exc)
                terminal = error.startswith(("configuration:", "authentication:", "invalid_response:"))
                job.status = "failed" if job.attempts >= MAX_ATTEMPTS or terminal else "retry"
                job.lease_until = None
                job.next_attempt_at = now + timedelta(seconds=min(30 * (2 ** max(job.attempts - 1, 0)), 600))
                job.last_error = error
                job.updated_at = now
                db.commit()
        return 0


async def run_due_jobs(limit: int = JOB_BATCH_SIZE, article_id: int | None = None) -> list[dict]:
    with SessionLocal() as db:
        ids = claim_due_jobs(db, limit=limit, article_id=article_id)
    result = []
    for job_id in ids:
        result.append({"articleId": job_id, "chunks": await process_index_job(job_id)})
    return result
