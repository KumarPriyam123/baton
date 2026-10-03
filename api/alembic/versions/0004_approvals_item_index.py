"""Index approvals by item (added in phase 3).

Revision ID: 0004
Revises: 0003

SPEC 3.2 lists no index on approvals except the partial unique one for pending requests. Two
request-path queries need the approvals of one item: the item view shows the newest approval
(`ORDER BY requested_at DESC LIMIT 1`) and a command checks whether a pending or approved approval
exists. Without this index both would scan `approvals`. See ENGINEERING_DECISIONS 23.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX approvals_item_idx ON approvals (item_id, requested_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS approvals_item_idx")
