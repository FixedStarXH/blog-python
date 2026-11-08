"""Preserve Go tag creation and update timestamps."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0007_tag_timestamps"
down_revision = "0006_article_index_jobs"
branch_labels = None
depends_on = None


def upgrade():
    timestamp_type = mysql.DATETIME(fsp=3).with_variant(sa.DateTime(), "sqlite")
    op.add_column("tags", sa.Column("created_at", timestamp_type, nullable=True))
    op.add_column("tags", sa.Column("updated_at", timestamp_type, nullable=True))


def downgrade():
    with op.batch_alter_table("tags") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
