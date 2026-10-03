"""Team keys and item identities never change (SPEC 3.2).

Revision ID: 0003
Revises: 0002

SPEC 3.2 says teams.key is immutable and work_items.key "never changes, even after transfer".
Item keys appear in URLs, messages and people's notes, and (origin_team_id, number) is what
makes them unique, so those three columns are locked together with the key. A CHECK cannot see
the old row, so these are BEFORE UPDATE triggers that fire only when one of the columns changes;
every other update (item_seq, version, status...) is untouched.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_identity_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '%.% is immutable: key, number and origin team never change',
                TG_TABLE_NAME, TG_ARGV[0]
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER teams_key_immutable
            BEFORE UPDATE ON teams
            FOR EACH ROW WHEN (OLD.key IS DISTINCT FROM NEW.key)
            EXECUTE FUNCTION reject_identity_change('key')
        """
    )
    op.execute(
        """
        CREATE TRIGGER work_items_identity_immutable
            BEFORE UPDATE ON work_items
            FOR EACH ROW WHEN (
                OLD.key IS DISTINCT FROM NEW.key
                OR OLD.number IS DISTINCT FROM NEW.number
                OR OLD.origin_team_id IS DISTINCT FROM NEW.origin_team_id
            )
            EXECUTE FUNCTION reject_identity_change('key/number/origin_team_id')
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS work_items_identity_immutable ON work_items")
    op.execute("DROP TRIGGER IF EXISTS teams_key_immutable ON teams")
    op.execute("DROP FUNCTION IF EXISTS reject_identity_change()")
