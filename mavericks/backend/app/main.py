from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app import demo_app
from app.api import routes_execution, routes_health, routes_migration, routes_qtest
from app.api.errors import ERROR_RESPONSES, register_error_handlers
from app.api.security import API_KEY_HEADER, require_api_key
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    docs = settings.docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", API_KEY_HEADER],
    )
    # Rejects requests whose Host header is not ours, so a DNS-rebinding page cannot reach the local API.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    register_error_handlers(app)

    app.include_router(routes_health.router, prefix="/api", responses=ERROR_RESPONSES)
    for module in (routes_migration, routes_execution, routes_qtest):
        app.include_router(
            module.router, prefix="/api", responses=ERROR_RESPONSES, dependencies=[Depends(require_api_key)]
        )
    if settings.demo_app_enabled:
        app.include_router(demo_app.router)
    return app


app = create_app()
