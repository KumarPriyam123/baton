from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_test_database_is_reachable_and_is_postgres_16(test_database_url: str) -> None:
    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
            version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()
    finally:
        await engine.dispose()

    assert str(version).startswith("16")
