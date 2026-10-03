"""Is the database reachable, and is it at the migration head this code expects?"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
CHECK_TIMEOUT_SECONDS = 3.0


def expected_head() -> str:
    """The newest revision in the migration files shipped with this code."""
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    heads = script.get_heads()
    assert len(heads) == 1, f"expected one migration head, found {heads}"
    return heads[0]


@dataclass(frozen=True)
class Readiness:
    database: bool
    migrations: bool
    current: str | None
    expected: str

    @property
    def ready(self) -> bool:
        return self.database and self.migrations

    def body(self) -> dict[str, object]:
        return {
            "status": "ok" if self.ready else "unavailable",
            "database": self.database,
            "migrations": self.migrations,
            "current": self.current,
            "expected": self.expected,
        }


async def check_ready(engine: AsyncEngine) -> Readiness:
    expected = expected_head()
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            try:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                current = result.scalar()
            except Exception:
                current = None
    except Exception:
        return Readiness(database=False, migrations=False, current=None, expected=expected)
    return Readiness(
        database=True, migrations=current == expected, current=current, expected=expected
    )
