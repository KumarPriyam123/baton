from fastapi import FastAPI

from app.api.request_context import RequestContextMiddleware
from app.api.routers import health
from app.config import Settings, get_settings
from app.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Baton",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    return app
