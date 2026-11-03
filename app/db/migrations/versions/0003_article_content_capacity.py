"""Increase article content capacity on MySQL.

Revision ID: 0003_article_content_capacity
Revises: 0002_user_email_index
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0003_article_content_capacity"
down_revision = "0002_user_email_index"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "mysql":
        op.alter_column("articles", "content", existing_type=sa.Text(),
                        type_=mysql.MEDIUMTEXT(), existing_nullable=False)


def downgrade():
    if op.get_bind().dialect.name == "mysql":
        op.alter_column("articles", "content", existing_type=mysql.MEDIUMTEXT(),
                        type_=sa.Text(), existing_nullable=False)
