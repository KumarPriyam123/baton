"""Structural guards (CLAUDE.md I1, I8): who may write work items and history, and no OFFSET.

These read the source, so a new write path or a paging shortcut fails here before it can ship.
"""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# The only modules that may write these tables. Everything else goes through them.
WORK_ITEM_WRITERS = {"repo/items.py", "db/tx.py"}
EVENT_WRITERS = {"db/tx.py"}

OUTBOX_WRITERS = {"db/tx.py", "repo/outbox.py"}  # enqueue; claim, complete, fail (the worker)

WRITES_WORK_ITEMS = re.compile(
    r"(?:insert|update|delete)\(\s*(?:schema\.)?work_items\b"
    r"|\bwork_items\s*\.\s*(?:insert|update|delete)\s*\("
    r"|\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+work_items\b"
    # an alias such as `t = schema.work_items` hides the table from the patterns above
    r"|=\s*schema\.work_items\s*$",
    re.IGNORECASE | re.MULTILINE,
)
WRITES_OUTBOX = re.compile(
    r"(?:insert|update|delete)\(\s*(?:schema\.)?outbox\b"
    r"|\boutbox\s*\.\s*(?:insert|update|delete)\s*\("
    r"|\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+outbox\b"
    r"|=\s*schema\.outbox\s*$",
    re.IGNORECASE | re.MULTILINE,
)
USES_NOTIFY = re.compile(r"pg_notify|\bNOTIFY\b")
WRITES_EVENTS = re.compile(
    r"(?:insert|update|delete)\(\s*(?:schema\.)?item_events\b"
    r"|\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+item_events\b",
    re.IGNORECASE,
)
USES_OFFSET = re.compile(r"\.\s*offset\s*\(|\bOFFSET\s+(?:\d|:|\$|%)", re.IGNORECASE)


def sources() -> list[tuple[str, str]]:
    return [
        (path.relative_to(APP).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(APP.rglob("*.py"))
    ]


def offenders(pattern: re.Pattern[str], allowed: set[str]) -> list[str]:
    return [name for name, text in sources() if name not in allowed and pattern.search(text)]


def test_only_the_item_repo_and_record_event_write_work_items() -> None:
    assert offenders(WRITES_WORK_ITEMS, WORK_ITEM_WRITERS) == []


def test_only_record_event_writes_item_events() -> None:
    assert offenders(WRITES_EVENTS, EVENT_WRITERS) == []


def test_only_record_event_writes_the_outbox_and_notifies() -> None:
    assert offenders(WRITES_OUTBOX, OUTBOX_WRITERS) == []
    assert offenders(USES_NOTIFY, {"db/tx.py"}) == []


def test_the_scanners_also_catch_method_forms_aliases_the_outbox_and_notify() -> None:
    assert WRITES_WORK_ITEMS.search("await conn.execute(schema.work_items.update().values(a=1))")
    assert WRITES_WORK_ITEMS.search("t = schema.work_items\nawait conn.execute(sa.update(t))")
    assert WRITES_OUTBOX.search("await conn.execute(sa.insert(schema.outbox).values(a=1))")
    assert WRITES_OUTBOX.search('text("UPDATE outbox SET status = 1")')
    assert USES_NOTIFY.search('text("SELECT pg_notify(:c, :p)")')


def test_the_scanners_do_catch_a_write_they_are_meant_to_forbid() -> None:
    assert WRITES_WORK_ITEMS.search("await conn.execute(sa.update(schema.work_items).values(a=1))")
    assert WRITES_WORK_ITEMS.search("await conn.execute(update(work_items).values(a=1))")
    assert WRITES_WORK_ITEMS.search('text("UPDATE work_items SET title = :t")')
    assert WRITES_WORK_ITEMS.search("pg_insert(schema.work_items).values(a=1)")
    assert WRITES_EVENTS.search("sa.insert(schema.item_events).values(kind='x')")
    assert WRITES_EVENTS.search('"INSERT INTO item_events (kind) VALUES (1)"')
    assert not WRITES_WORK_ITEMS.search("sa.select(schema.work_items.c.id)")


@pytest.mark.parametrize(
    "sample",
    [
        "query.offset(20)",
        "select(x).offset( page * size )",
        'text("SELECT * FROM t LIMIT 10 OFFSET 20")',
        'text("SELECT * FROM t LIMIT :n OFFSET :o")',
    ],
)
def test_the_offset_scanner_catches_offset_paging(sample: str) -> None:
    assert USES_OFFSET.search(sample)


def test_no_request_path_pages_with_offset() -> None:
    assert offenders(USES_OFFSET, set()) == []
