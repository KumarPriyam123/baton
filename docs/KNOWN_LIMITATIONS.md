# Known limitations

What Baton does not do, or does only partly, and what it would take. The out-of-scope list in
SPEC section 15 is added in phase 14; this file starts with limitations found while building.

## History is append-only for UPDATE and DELETE, not for TRUNCATE

`item_events` has a trigger that rejects UPDATE and DELETE (SPEC 3.2, CLAUDE.md I9). A
statement-level `TRUNCATE` is not covered, and `scripts/seed.py --reset` uses it to wipe the
demo data. Anyone connected as the table owner can therefore still erase the audit trail.

- **Why it is left open:** the seed's reset needs it, and the app never issues TRUNCATE.
- **What it would take:** run the API and worker as a role that owns nothing and has no TRUNCATE
  privilege (`REVOKE TRUNCATE ON ALL TABLES`), keeping the owner role for migrations and the seed;
  optionally a `BEFORE TRUNCATE` statement trigger that the seed bypasses explicitly.
