"""Preserve microseconds in MySQL article publication times.

Revision ID: 0004_publish_time_precision
Revises: 0003_article_content_capacity
"""
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0004_publish_time_precision"
down_revision = "0003_article_content_capacity"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "mysql":
        op.alter_column("articles", "publish_at", existing_type=mysql.DATETIME(),
                        type_=mysql.DATETIME(fsp=6), existing_nullable=True)


def downgrade():
    if op.get_bind().dialect.name == "mysql":
        op.alter_column("articles", "publish_at", existing_type=mysql.DATETIME(fsp=6),
                        type_=mysql.DATETIME(), existing_nullable=True)
