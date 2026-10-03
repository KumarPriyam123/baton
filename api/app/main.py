from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.csrf import CsrfMiddleware
from app.api.errors import install_error_handlers
from app.api.request_context import RequestContextMiddleware
from app.api.routers import auth, demo, health, item_commands, items, me, teams, users
from app.config import Settings, get_settings
from app.db.engine import create_engine
from app.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Baton",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
        # The operation id is the function name: stable when a route moves, and the name the
        # generated TypeScript client exposes.
        generate_unique_id_function=lambda route: route.name,
    )
    app.state.settings = settings
    install_error_handlers(app)
    # The last middleware added is the outermost: the request id is assigned first, so every
    # response (including a CSRF rejection) carries it, then CSRF is checked, then routing.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(demo.router)
    app.include_router(teams.router)
    app.include_router(users.router)
    app.include_router(items.router)
    app.include_router(item_commands.router)
    return app
