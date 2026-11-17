from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.models.base import CamelModel

router = APIRouter(tags=["health"])


class HealthResponse(CamelModel):
    status: str
    app_name: str
    version: str
    llm_provider: str
    qtest_mode: str
    auth_required: bool


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        version=settings.app_version,
        llm_provider=settings.llm_provider,
        qtest_mode=settings.qtest_mode,
        auth_required=settings.api_key is not None,
    )
