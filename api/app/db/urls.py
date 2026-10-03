"""Helpers for the two spellings of a database URL (SQLAlchemy vs asyncpg)."""

from sqlalchemy.engine import make_url


def asyncpg_dsn(url: str) -> str:
    """postgresql+asyncpg://... -> postgresql://... (what asyncpg.connect expects)."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def with_database(url: str, database: str) -> str:
    """Same server and credentials, different database name."""
    return make_url(url).set(database=database).render_as_string(hide_password=False)
