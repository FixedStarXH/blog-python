"""Preserve millisecond precision in imported Go tag timestamps."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0008_tag_timestamp_precision"
down_revision = "0007_tag_timestamps"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "mysql":
        return
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "tags", column,
            existing_type=sa.DateTime(),
            type_=mysql.DATETIME(fsp=3),
            existing_nullable=True,
        )


def downgrade():
    if op.get_bind().dialect.name != "mysql":
        return
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "tags", column,
            existing_type=mysql.DATETIME(fsp=3),
            type_=sa.DateTime(),
            existing_nullable=True,
        )
