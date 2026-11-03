"""Add the user email lookup index.

Revision ID: 0002_user_email_index
Revises: 0001_initial
"""
from alembic import op

revision = "0002_user_email_index"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_users_email", "users", ["email"])


def downgrade():
    op.drop_index("ix_users_email", table_name="users")
