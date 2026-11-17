from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.dependencies import get_qtest_service
from app.models.fields import MIGRATION_ID_PATTERN
from app.models.migration_request import QTestSyncRequest
from app.models.traceability import QTestLink, TraceabilityReport
from app.services.qtest_service import QTestService

router = APIRouter(prefix="/qtest", tags=["qtest"])


@router.post("/sync", response_model=list[QTestLink])
def sync(request: QTestSyncRequest, service: Annotated[QTestService, Depends(get_qtest_service)]) -> list[QTestLink]:
    return service.sync(request.migration_id)


@router.get("/{migration_id}/traceability", response_model=TraceabilityReport)
def traceability(
    migration_id: Annotated[str, Path(pattern=MIGRATION_ID_PATTERN)],
    service: Annotated[QTestService, Depends(get_qtest_service)],
) -> TraceabilityReport:
    return service.traceability(migration_id)
