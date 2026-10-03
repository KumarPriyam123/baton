"""Idempotency keys (SPEC 6.3): a repeated request gets the first request's answer.

Everything happens in the command's own transaction, so the key and the change can never
disagree after a crash:

1. INSERT the key ON CONFLICT DO NOTHING. If another request holds the same key and is still
   running, Postgres makes this insert wait for it to finish; there is no check-then-insert gap.
2. Inserted: run the command inside a SAVEPOINT.
   - success: store status + body on the key row, commit;
   - domain error (4xx): roll back to the savepoint, store the error, commit (a retry gets the
     same answer);
   - anything else (5xx, BUSY): roll back everything, key included, so a retry runs again.
3. Not inserted: the same fingerprint replays the stored answer; another one is
   422 IDEMPOTENCY_KEY_REUSED.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import Depends, Header, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.problem import problem_body
from app.db import errors, schema
from app.db.tx import CommandTx, run_command
from app.domain.errors import DomainError, IdempotencyKeyReused, ValidationFailed

KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
_VALID_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")  # printable ASCII, no spaces


@dataclass(frozen=True)
class Outcome:
    """What a command answered: stored with the key, replayed on a repeat."""

    status: int
    body: dict[str, Any]
    replayed: bool = False

    @property
    def is_error(self) -> bool:
        return self.status >= 400


def parse_key(raw: str | None, *, required: bool) -> str | None:
    if raw is None or raw == "":
        if required:
            raise ValidationFailed(
                "This request needs an Idempotency-Key header.",
                errors=[
                    {
                        "field": KEY_HEADER,
                        "message": "Required. Use one new random value per user action.",
                        "type": "missing",
                    }
                ],
            )
        return None
    if not _VALID_KEY.fullmatch(raw):
        raise ValidationFailed(
            "The Idempotency-Key is not valid.",
            errors=[
                {
                    "field": KEY_HEADER,
                    "message": "1 to 255 printable ASCII characters, no spaces.",
                    "type": "value_error",
                }
            ],
        )
    return raw


def optional_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias=KEY_HEADER,
            description="One new random value per user action; reuse it on every retry.",
        ),
    ] = None,
) -> str | None:
    return parse_key(idempotency_key, required=False)


def required_key(
    idempotency_key: Annotated[
        str,
        Header(
            alias=KEY_HEADER,
            description="Required. One new random value per user action; reuse it on every retry.",
        ),
    ],
) -> str:
    parsed = parse_key(idempotency_key, required=True)
    assert parsed is not None
    return parsed


OptionalIdempotencyKey = Annotated[str | None, Depends(optional_key)]
RequiredIdempotencyKey = Annotated[str, Depends(required_key)]


async def fingerprint(request: Request) -> str:
    """sha256 of method + path + canonical body (SPEC 3.2): the same request, however the JSON
    was spaced or ordered, hashes the same."""
    raw = await request.body()
    try:
        canonical = json.dumps(
            json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except ValueError:
        canonical = raw.decode("utf-8", errors="replace")
    material = f"{request.method}\n{request.url.path}\n{canonical}"
    return hashlib.sha256(material.encode("utf-8", errors="surrogatepass")).hexdigest()


Work = Callable[[CommandTx], Awaitable[Outcome]]


async def run_idempotent(
    conn: AsyncConnection,
    *,
    actor_id: uuid.UUID,
    request_id: str | None,
    key: str | None,
    request_fingerprint: str,
    work: Work,
    now: datetime | None = None,
) -> Outcome:
    """Run `work` as one command, once per (user, key). Without a key it is just a command."""

    async def attempt(tx: CommandTx) -> Outcome:
        if key is None:
            return await work(tx)

        t = schema.idempotency_keys
        inserted = await tx.conn.execute(
            pg_insert(t)
            .values(user_id=actor_id, key=key, fingerprint=request_fingerprint, created_at=tx.now)
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(t.c.key)
        )
        if inserted.first() is None:
            return await _replay(tx.conn, actor_id, key, request_fingerprint)

        try:
            async with tx.conn.begin_nested():
                outcome = await work(tx)
        except DomainError as error:
            if error.status >= 500:
                raise
            outcome = _error_outcome(error, request_id)
        except DBAPIError as error:
            mapped = errors.translate(error)  # a constraint that fired; BUSY is a 503, not stored
            if mapped is None or mapped.status >= 500:
                raise
            outcome = _error_outcome(mapped, request_id)

        await tx.conn.execute(
            sa.update(t)
            .where(t.c.user_id == actor_id, t.c.key == key)
            .values(response_status=outcome.status, response_body=outcome.body)
        )
        return outcome

    return await run_command(conn, attempt, actor_id=actor_id, request_id=request_id, now=now)


def _error_outcome(error: DomainError, request_id: str | None) -> Outcome:
    body = problem_body(
        status=error.status,
        code=error.code,
        title=error.title,
        detail=error.detail,
        request_id=request_id,
        errors=error.errors,
        current=error.current,
        changes_since=error.changes_since,
    )
    return Outcome(error.status, body)


async def _replay(conn: AsyncConnection, user_id: uuid.UUID, key: str, wanted: str) -> Outcome:
    t = schema.idempotency_keys.c
    row = (
        await conn.execute(
            sa.select(t.fingerprint, t.response_status, t.response_body).where(
                t.user_id == user_id, t.key == key
            )
        )
    ).one()
    if row.fingerprint != wanted:
        raise IdempotencyKeyReused(
            "This Idempotency-Key was already used for a different request. "
            "Use a new key for each user action."
        )
    return Outcome(row.response_status, row.response_body, replayed=True)
