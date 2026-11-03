import hashlib, json
from datetime import datetime
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from app.core.errors import DomainError
from app.integrations.ai.client import embed
from app.core.config import settings
from app.models import Article, ArticleChunk
from app.rag.chunking import split_chunks


async def index_article(db: Session, article: Article):
    if article.status!=1 or article.password or article.deleted_at or (article.publish_at and article.publish_at > datetime.utcnow()):
        db.execute(delete(ArticleChunk).where(ArticleChunk.article_id==article.id)); db.commit(); return 0
    chunks=split_chunks(article.content)
    if not chunks: return 0
    vectors=await embed(chunks)
    rows=[ArticleChunk(article_id=article.id,chunk_index=i,content=chunk,embedding=json.dumps(vec).encode(),embedding_model=settings.embedding_model,embedding_dim=len(vec),content_hash=hashlib.sha256(chunk.encode()).hexdigest(),indexed_at=datetime.utcnow()) for i,(chunk,vec) in enumerate(zip(chunks,vectors))]
    try:
        db.execute(delete(ArticleChunk).where(ArticleChunk.article_id==article.id)); db.add_all(rows); db.commit()
    except Exception:
        db.rollback(); raise
    return len(rows)
