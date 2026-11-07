from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_editor
from app.core.errors import DomainError, envelope
from app.models import Article, ArticleChunk, ArticleIndexJob, User
from app.rag.jobs import enqueue_article, run_due_jobs

router=APIRouter(prefix="/admin/ai")


@router.post("/index/{article_id}")
async def index_one(article_id: int, db: Session=Depends(get_db), user: User=Depends(require_editor)):
    article=db.get(Article,article_id)
    if not article or article.deleted_at: raise DomainError("article not found",404)
    enqueue_article(db,article,force=True); db.commit()
    result=await run_due_jobs(article_id=article_id)
    job=db.get(ArticleIndexJob,article_id)
    return envelope({"indexed":result[0]["chunks"] if result else 0,
                     "status":job.status if job else "removed",
                     "error":job.last_error if job and job.last_error else None})


@router.post("/index-all")
async def index_all(db: Session=Depends(get_db), user: User=Depends(require_editor)):
    articles=db.scalars(select(Article).where(Article.status==1,Article.password=="",Article.deleted_at.is_(None))).all()
    for article in articles:
        enqueue_article(db,article,force=True)
    db.commit()
    result=await run_due_jobs(limit=max(len(articles),1))
    return envelope({"articles":len(articles),"indexed":sum(x["chunks"] for x in result),
                     "queued":sum(1 for x in result if not x["chunks"])})


@router.get("/index-status")
def index_status(db: Session=Depends(get_db), user: User=Depends(require_editor)):
    count=db.scalar(select(func.count(ArticleChunk.id))) or 0
    job_counts={status:total for status,total in db.execute(
        select(ArticleIndexJob.status,func.count()).group_by(ArticleIndexJob.status)
    ).all()}
    jobs=db.scalars(select(ArticleIndexJob).order_by(ArticleIndexJob.updated_at.desc()).limit(100)).all()
    return envelope({"chunks":count,"indexedArticles":db.scalar(select(func.count(func.distinct(ArticleChunk.article_id)))) or 0,
                     "jobs":{"pending":job_counts.get("pending",0),"processing":job_counts.get("processing",0),
                             "retry":job_counts.get("retry",0),"failed":job_counts.get("failed",0),
                             "complete":job_counts.get("complete",0)},
                     "recentFailures":[{"articleId":x.article_id,"attempts":x.attempts,"error":x.last_error}
                                       for x in jobs if x.status=="failed"][:20]})
