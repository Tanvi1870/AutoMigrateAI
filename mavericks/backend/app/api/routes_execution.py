from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_execution_service
from app.models.execution_result import ExecutionResult
from app.models.migration_request import ExecuteRequest
from app.services.execution_service import ExecutionService

router = APIRouter(prefix="/migration", tags=["execution"])


@router.post("/execute", response_model=ExecutionResult)
def execute(
    request: ExecuteRequest, service: Annotated[ExecutionService, Depends(get_execution_service)]
) -> ExecutionResult:
    return service.execute(request.migration_id, request.target_framework)
