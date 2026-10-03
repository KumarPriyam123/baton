"""The async SQLAlchemy engine, created once per API process in the app lifespan."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "application_name": "baton-api",
                # A runaway read must not pin a connection. Commands set tighter limits per
                # transaction (SPEC 6.4).
                "statement_timeout": "10000",
            }
        },
    )
