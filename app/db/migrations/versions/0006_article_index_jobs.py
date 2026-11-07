"""Persist and coalesce article embedding work."""
from alembic import op
import sqlalchemy as sa


revision = "0006_article_index_jobs"
down_revision = "0005_view_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "article_index_jobs",
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_index("ix_article_index_jobs_content_hash", "article_index_jobs", ["content_hash"])
    op.create_index("ix_article_index_jobs_status", "article_index_jobs", ["status"])
    op.create_index("ix_article_index_jobs_next_attempt_at", "article_index_jobs", ["next_attempt_at"])
    op.create_index("ix_article_index_jobs_lease_until", "article_index_jobs", ["lease_until"])


def downgrade():
    op.drop_index("ix_article_index_jobs_lease_until", table_name="article_index_jobs")
    op.drop_index("ix_article_index_jobs_next_attempt_at", table_name="article_index_jobs")
    op.drop_index("ix_article_index_jobs_status", table_name="article_index_jobs")
    op.drop_index("ix_article_index_jobs_content_hash", table_name="article_index_jobs")
    op.drop_table("article_index_jobs")
