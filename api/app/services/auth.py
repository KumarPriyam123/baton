"""Sign in. The password is checked (slow, CPU) before the command transaction starts; the
writes (failure count, or session) then run in one short command transaction (CLAUDE.md I5).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from app.auth import passwords, sessions
from app.db.tx import CommandTx, run_command
from app.domain.errors import RateLimited, Unauthenticated
from app.repo import users as users_repo

MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)

# One message for an unknown email and a wrong password (SPEC 5.4).
INVALID_CREDENTIALS = "Invalid email or password."


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    csrf_token: str
    user_id: str


def _locked(locked_until: datetime, now: datetime) -> RateLimited:
    seconds = max(1, math.ceil((locked_until - now).total_seconds()))
    minutes = math.ceil(seconds / 60)
    return RateLimited(
        f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )


async def login(
    conn: AsyncConnection,
    *,
    email: str,
    password: str,
    user_agent: str | None,
    now: datetime,
    session_ttl: timedelta,
) -> LoginResult:
    user = await users_repo.find_by_email(conn, email)
    if user is None:
        await passwords.verify_against_nothing(password)
        raise Unauthenticated(INVALID_CREDENTIALS)

    # A locked account is refused before the password is even checked, so a lock cannot be
    # used to test guesses and the right password is not confirmed during the lock.
    if user.locked_until is not None and user.locked_until > now:
        raise _locked(user.locked_until, now)

    matches = await passwords.averify(user.password_hash, password)
    if not matches or user.deactivated_at is not None:
        if user.deactivated_at is None:

            async def count_failure(tx: CommandTx) -> None:
                await users_repo.register_failed_login(
                    tx.conn,
                    user.id,
                    now=now,
                    max_failures=MAX_FAILED_LOGINS,
                    lock_until=now + LOCK_DURATION,
                )

            # Committed first, then refused: raising inside the transaction would undo the count.
            await run_command(conn, count_failure, now=now)
        raise Unauthenticated(INVALID_CREDENTIALS)

    token = sessions.new_token()

    async def start_session(tx: CommandTx) -> None:
        await users_repo.clear_failed_logins(tx.conn, user.id)
        await sessions.create_session(tx.conn, token, user.id, user_agent, now, session_ttl)

    await run_command(conn, start_session, actor_id=user.id, now=now)
    return LoginResult(token, sessions.new_csrf_token(), str(user.id))
