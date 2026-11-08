from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, Table, Column, true
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.mysql import DATETIME, MEDIUMTEXT
from app.db.base import Base

article_tags = Table(
    "article_tags", Base.metadata,
    Column("article_id", ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)
_TAG_TIMESTAMP_TYPE = DateTime().with_variant(DATETIME(fsp=3), "mysql")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username"), UniqueConstraint("email"))
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), index=True)
    email: Mapped[str] = mapped_column(String(100), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50), default="")
    avatar: Mapped[str] = mapped_column(String(255), default="")
    bio: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[int] = mapped_column(Integer, default=1, index=True)
    status: Mapped[int] = mapped_column(Integer, default=1, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(200), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime | None] = mapped_column(_TAG_TIMESTAMP_TYPE, nullable=True, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(_TAG_TIMESTAMP_TYPE, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    articles = relationship("Article", secondary=article_tags, back_populates="tags")


class ColumnModel(Base):
    __tablename__ = "columns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(255), default="")
    cover: Mapped[str] = mapped_column(String(255), default="")
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"))
    summary: Mapped[str] = mapped_column(String(500), default="")
    cover_image: Mapped[str] = mapped_column(String(255), default="")
    source_url: Mapped[str] = mapped_column(String(255), default="")
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reject_reason: Mapped[str] = mapped_column(String(255), default="")
    is_top: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=True, index=True)
    password: Mapped[str] = mapped_column(String(255), default="")
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    column_id: Mapped[int | None] = mapped_column(ForeignKey("columns.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    author = relationship("User")
    category = relationship("Category")
    column = relationship("ColumnModel")
    tags = relationship("Tag", secondary=article_tags, back_populates="articles")


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=0, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    nickname: Mapped[str] = mapped_column(String(20), default="游客")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id"), nullable=True, index=True)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MetricEvent(Base):
    __tablename__ = "article_metrics"
    __table_args__ = (UniqueConstraint("article_id", "kind", "fingerprint"), Index("ix_metric_pending_views", "kind", "counted", "id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    counted: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SiteSetting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Link(Base):
    __tablename__ = "links"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(200), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Image(Base):
    __tablename__ = "images"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    ext: Mapped[str] = mapped_column(String(10))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    uploader: Mapped[User] = relationship(foreign_keys=[uploader_id], lazy="joined")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(100), index=True)
    method: Mapped[str] = mapped_column(String(10), default="")
    path: Mapped[str] = mapped_column(String(200), default="")
    target_type: Mapped[str] = mapped_column(String(50), default="")
    target_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    detail: Mapped[str] = mapped_column(String(500), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")
    status: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ArticleChunk(Base):
    __tablename__ = "article_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), default="")
    embedding_dim: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ArticleIndexJob(Base):
    """Durable, coalesced indexing work for the latest public article version."""
    __tablename__ = "article_index_jobs"
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding_model: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


Index("ix_articles_public", Article.status, Article.deleted_at, Article.created_at)
