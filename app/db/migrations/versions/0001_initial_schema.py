"""Initial Blog System schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id",sa.Integer(),primary_key=True), sa.Column("username",sa.String(50),nullable=False),
        sa.Column("email",sa.String(100),nullable=False), sa.Column("password_hash",sa.String(255),nullable=False),
        sa.Column("nickname",sa.String(50),nullable=False,server_default=""), sa.Column("avatar",sa.String(255),nullable=False,server_default=""),
        sa.Column("bio",sa.String(255),nullable=False,server_default=""), sa.Column("role",sa.Integer(),nullable=False,server_default="1"),
        sa.Column("status",sa.Integer(),nullable=False,server_default="1"), sa.Column("created_at",sa.DateTime(),nullable=False),
        sa.Column("updated_at",sa.DateTime(),nullable=False), sa.Column("deleted_at",sa.DateTime(),nullable=True),
        sa.UniqueConstraint("username"),sa.UniqueConstraint("email"))
    op.create_index("ix_users_username","users",["username"]); op.create_index("ix_users_role","users",["role"])
    op.create_index("ix_users_status","users",["status"]); op.create_index("ix_users_deleted_at","users",["deleted_at"])
    op.create_table("categories",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("name",sa.String(50),nullable=False),sa.Column("description",sa.String(200),nullable=False,server_default=""),sa.Column("sort",sa.Integer(),nullable=False,server_default="0"),sa.Column("deleted_at",sa.DateTime(),nullable=True),sa.UniqueConstraint("name"))
    op.create_table("tags",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("name",sa.String(50),nullable=False),sa.Column("deleted_at",sa.DateTime(),nullable=True),sa.UniqueConstraint("name"))
    op.create_table("columns",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("name",sa.String(100),nullable=False),sa.Column("description",sa.String(255),nullable=False,server_default=""),sa.Column("cover",sa.String(255),nullable=False,server_default=""),sa.Column("author_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),sa.Column("status",sa.Integer(),nullable=False,server_default="1"),sa.Column("deleted_at",sa.DateTime(),nullable=True))
    op.create_index("ix_columns_author_id","columns",["author_id"])
    op.create_table("articles",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("title",sa.String(200),nullable=False),sa.Column("content",sa.Text(),nullable=False),
        sa.Column("summary",sa.String(500),nullable=False,server_default=""),sa.Column("cover_image",sa.String(255),nullable=False,server_default=""),
        sa.Column("source_url",sa.String(255),nullable=False,server_default=""),sa.Column("view_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("like_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("status",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("reject_reason",sa.String(255),nullable=False,server_default=""),sa.Column("is_top",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("is_featured",sa.Boolean(),nullable=False,server_default=sa.false()),sa.Column("publish_at",sa.DateTime(),nullable=True),
        sa.Column("password",sa.String(255),nullable=False,server_default=""),sa.Column("author_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("category_id",sa.Integer(),sa.ForeignKey("categories.id"),nullable=False),sa.Column("column_id",sa.Integer(),sa.ForeignKey("columns.id"),nullable=True),
        sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),sa.Column("deleted_at",sa.DateTime(),nullable=True))
    for name,col in [("ix_articles_title","title"),("ix_articles_status","status"),("ix_articles_is_top","is_top"),("ix_articles_is_featured","is_featured"),("ix_articles_publish_at","publish_at"),("ix_articles_author_id","author_id"),("ix_articles_category_id","category_id"),("ix_articles_column_id","column_id"),("ix_articles_created_at","created_at"),("ix_articles_deleted_at","deleted_at")]: op.create_index(name,"articles",[col])
    op.create_index("ix_articles_public","articles",["status","deleted_at","created_at"])
    op.create_table("article_tags",sa.Column("article_id",sa.Integer(),sa.ForeignKey("articles.id",ondelete="CASCADE"),primary_key=True),sa.Column("tag_id",sa.Integer(),sa.ForeignKey("tags.id",ondelete="CASCADE"),primary_key=True))
    op.create_table("comments",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("content",sa.Text(),nullable=False),sa.Column("status",sa.Integer(),nullable=False,server_default="0"),sa.Column("article_id",sa.Integer(),sa.ForeignKey("articles.id"),nullable=False),sa.Column("user_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=True),sa.Column("nickname",sa.String(20),nullable=False,server_default="游客"),sa.Column("parent_id",sa.Integer(),sa.ForeignKey("comments.id"),nullable=True),sa.Column("like_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("deleted_at",sa.DateTime(),nullable=True))
    for name,col in [("ix_comments_status","status"),("ix_comments_article_id","article_id"),("ix_comments_user_id","user_id"),("ix_comments_parent_id","parent_id"),("ix_comments_created_at","created_at")]: op.create_index(name,"comments",[col])
    op.create_table("article_metrics",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("article_id",sa.Integer(),sa.ForeignKey("articles.id",ondelete="CASCADE"),nullable=False),sa.Column("kind",sa.String(12),nullable=False),sa.Column("fingerprint",sa.String(64),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),sa.UniqueConstraint("article_id","kind","fingerprint"))
    op.create_index("ix_article_metrics_article_id","article_metrics",["article_id"]);op.create_index("ix_article_metrics_kind","article_metrics",["kind"]);op.create_index("ix_article_metrics_created_at","article_metrics",["created_at"])
    op.create_table("refresh_tokens",sa.Column("token_hash",sa.String(64),primary_key=True),sa.Column("user_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),sa.Column("expires_at",sa.DateTime(),nullable=False),sa.Column("revoked_at",sa.DateTime(),nullable=True))
    op.create_index("ix_refresh_tokens_user_id","refresh_tokens",["user_id"])
    op.create_table("settings",sa.Column("key",sa.String(100),primary_key=True),sa.Column("value",sa.Text(),nullable=False,server_default=""))
    op.create_table("links",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("name",sa.String(50),nullable=False),sa.Column("url",sa.String(255),nullable=False),sa.Column("description",sa.String(200),nullable=False,server_default=""),sa.Column("sort",sa.Integer(),nullable=False,server_default="0"),sa.Column("enabled",sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column("deleted_at",sa.DateTime(),nullable=True))
    op.create_table("images",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("name",sa.String(255),nullable=False),sa.Column("url",sa.String(255),nullable=False),sa.Column("size",sa.Integer(),nullable=False),sa.Column("ext",sa.String(10),nullable=False),sa.Column("uploader_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False))
    op.create_index("ix_images_uploader_id","images",["uploader_id"])
    op.create_table("audit_logs",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("operator_id",sa.Integer(),nullable=False),sa.Column("username",sa.String(50),nullable=False,server_default=""),sa.Column("role",sa.Integer(),nullable=False,server_default="0"),sa.Column("action",sa.String(100),nullable=False),sa.Column("method",sa.String(10),nullable=False,server_default=""),sa.Column("path",sa.String(200),nullable=False,server_default=""),sa.Column("target_type",sa.String(50),nullable=False,server_default=""),sa.Column("target_id",sa.Integer(),nullable=False,server_default="0"),sa.Column("detail",sa.String(500),nullable=False,server_default=""),sa.Column("ip",sa.String(45),nullable=False,server_default=""),sa.Column("status",sa.Integer(),nullable=False,server_default="1"),sa.Column("created_at",sa.DateTime(),nullable=False))
    for name,col in [("ix_audit_logs_operator_id","operator_id"),("ix_audit_logs_action","action"),("ix_audit_logs_target_id","target_id"),("ix_audit_logs_created_at","created_at")]: op.create_index(name,"audit_logs",[col])
    op.create_table("article_chunks",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("article_id",sa.Integer(),sa.ForeignKey("articles.id",ondelete="CASCADE"),nullable=False),sa.Column("chunk_index",sa.Integer(),nullable=False),sa.Column("content",sa.Text(),nullable=False),sa.Column("embedding",sa.LargeBinary(),nullable=True),sa.Column("embedding_model",sa.String(100),nullable=False,server_default=""),sa.Column("embedding_dim",sa.Integer(),nullable=False,server_default="0"),sa.Column("content_hash",sa.String(64),nullable=False,server_default=""),sa.Column("indexed_at",sa.DateTime(),nullable=False))
    for name,col in [("ix_article_chunks_article_id","article_id"),("ix_article_chunks_chunk_index","chunk_index"),("ix_article_chunks_content_hash","content_hash")]: op.create_index(name,"article_chunks",[col])


def downgrade():
    for table in ("article_chunks","audit_logs","images","links","settings","refresh_tokens","article_metrics","comments","article_tags","articles","columns","tags","categories","users"):
        op.drop_table(table)
