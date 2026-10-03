"""item_reads tracks the last event a user saw, not the version.

Revision ID: 0002
Revises: 0001

Comments no longer change `work_items.version` (decision 8), so a version cannot say whether
someone has seen a new comment. An event id can. The column keeps its position: it is renamed,
widened to bigint, and its values are converted in place.

upgrade:   "version V seen"  ->  the newest event of that item with item_version <= V
downgrade: "event E seen"    ->  E's item_version

Both conversions are lossy only in the way the two meanings differ (comments after V on the
old scale are not distinguishable); that is acceptable for read markers.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE item_reads RENAME COLUMN last_read_version TO last_read_event_id")
    op.execute("ALTER TABLE item_reads ALTER COLUMN last_read_event_id TYPE bigint")
    op.execute(
        """
        UPDATE item_reads r
           SET last_read_event_id = coalesce(
               (SELECT max(e.id) FROM item_events e
                 WHERE e.item_id = r.item_id AND e.item_version <= r.last_read_event_id),
               0)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE item_reads r
           SET last_read_event_id = coalesce(
               (SELECT e.item_version FROM item_events e WHERE e.id = r.last_read_event_id),
               1)
        """
    )
    op.execute("ALTER TABLE item_reads ALTER COLUMN last_read_event_id TYPE integer")
    op.execute("ALTER TABLE item_reads RENAME COLUMN last_read_event_id TO last_read_version")
