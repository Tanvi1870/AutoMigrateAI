from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, UploadFile, status

from app.config import Settings, get_settings
from app.dependencies import (
    get_migration_service,
    get_repair_service,
    get_report_service,
    get_validation_service,
)
from app.models.enums import TargetFramework
from app.models.fields import MIGRATION_ID_PATTERN
from app.models.keyword_resolution import KeywordResolution
from app.models.migration_plan import MigrationPlan
from app.models.migration_request import (
    AnalyzeRequest,
    GenerateRequest,
    RepairRequest,
    ValidateRequest,
)
from app.models.migration_result import GeneratedProject, UploadResponse
from app.models.repair import RepairResult
from app.models.report import MigrationReport
from app.models.validation_result import ValidationResult
from app.services.migration_service import MigrationService
from app.services.repair_service import RepairService
from app.services.report_service import ReportService
from app.services.validation_service import ValidationService

router = APIRouter(prefix="/migration", tags=["migration"])


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile,
    service: Annotated[MigrationService, Depends(get_migration_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UploadResponse:
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"Upload exceeds {settings.max_upload_bytes} bytes")
    return service.upload(file.filename or "", content)


@router.post("/analyze", response_model=MigrationPlan)
def analyze(
    request: AnalyzeRequest, service: Annotated[MigrationService, Depends(get_migration_service)]
) -> MigrationPlan:
    return service.analyze(request.migration_id, request.target_framework)


@router.post("/generate", response_model=GeneratedProject)
def generate(
    request: GenerateRequest, service: Annotated[MigrationService, Depends(get_migration_service)]
) -> GeneratedProject:
    return service.generate(request.migration_id, request.target_framework, request.simulate_locator_failure)


@router.post("/validate", response_model=ValidationResult)
def validate(
    request: ValidateRequest, service: Annotated[ValidationService, Depends(get_validation_service)]
) -> ValidationResult:
    return service.validate(request.migration_id, request.target_framework)


@router.post("/repair", response_model=RepairResult)
def repair(
    request: RepairRequest, service: Annotated[RepairService, Depends(get_repair_service)]
) -> RepairResult:
    return service.repair(request.migration_id, request.target_framework, request.apply)


@router.get("/{migration_id}/keywords", response_model=list[KeywordResolution])
def keyword_mappings(
    migration_id: Annotated[str, Path(pattern=MIGRATION_ID_PATTERN)],
    service: Annotated[MigrationService, Depends(get_migration_service)],
    target_framework: Annotated[TargetFramework, Query(alias="targetFramework")] = TargetFramework.PLAYWRIGHT,
) -> list[KeywordResolution]:
    return service.keyword_mappings(migration_id, target_framework)


@router.get("/{migration_id}/repairs", response_model=list[RepairResult])
def repair_history(
    migration_id: Annotated[str, Path(pattern=MIGRATION_ID_PATTERN)],
    service: Annotated[RepairService, Depends(get_repair_service)],
) -> list[RepairResult]:
    return service.history(migration_id)


@router.get("/{migration_id}/report", response_model=MigrationReport)
def report(
    migration_id: Annotated[str, Path(pattern=MIGRATION_ID_PATTERN)],
    service: Annotated[ReportService, Depends(get_report_service)],
    target_framework: Annotated[TargetFramework | None, Query(alias="targetFramework")] = None,
) -> MigrationReport:
    return service.build_report(migration_id, target_framework)
