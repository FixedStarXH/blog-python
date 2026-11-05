"""Track whether a durable view event has been included in the counter."""
from alembic import op
import sqlalchemy as sa

revision = "0005_view_outbox"
down_revision = "0004_publish_time_precision"
branch_labels = None
depends_on = None


def upgrade():
    # Existing view events were already counted by the previous implementation.
    op.add_column("article_metrics", sa.Column("counted", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_metric_pending_views", "article_metrics", ["kind", "counted", "id"])


def downgrade():
    # Pending events must be flushed before downgrading or their counts are lost.
    pending = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM article_metrics WHERE kind = 'view' AND counted = 0")).scalar_one()
    if pending:
        raise RuntimeError("Flush pending view events before downgrading")
    op.drop_index("ix_metric_pending_views", table_name="article_metrics")
    op.drop_column("article_metrics", "counted")
