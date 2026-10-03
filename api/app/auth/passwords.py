"""Password hashing: argon2id (SPEC 5.4).

Verifying costs tens of milliseconds of CPU, so the async entry point runs it in a worker thread
instead of blocking the event loop for every other request.
"""

import asyncio
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("not-a-real-password")


async def averify(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)


async def verify_against_nothing(password: str) -> None:
    """Spend the same time as a real check, so an unknown email is not faster than a wrong
    password (the response would otherwise reveal which emails have accounts)."""
    await averify(_dummy_hash(), password)
