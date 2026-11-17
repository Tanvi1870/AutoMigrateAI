from datetime import datetime

from pydantic import Field, computed_field

from app.models.base import CamelModel
from app.models.demo import DemoFault
from app.models.enums import CheckStatus, ExecutionStatus, TargetFramework
from app.models.execution_result import ExecutionResult
from app.models.fields import MigrationId, Name
from app.models.locator_resolution import LocatorResolution
from app.models.migration_plan import MigrationPlan
from app.models.repair import RepairResult
from app.models.traceability import QTestLink, TraceabilityRecord
from app.models.validation_result import ValidationResult


class MigrationMetrics(CamelModel):
    """Counts come from the migration itself; the manual baseline is a user-supplied estimate."""

    total_steps: int = Field(default=0, ge=0)
    keywords_detected: int = Field(default=0, ge=0)
    keywords_mapped: int | None = Field(default=None, ge=0)  # known once the migration is analyzed
    locators_resolved: int = Field(default=0, ge=0)
    locators_unresolved: int = Field(default=0, ge=0)
    generated_files: int = Field(default=0, ge=0)
    generated_lines: int = Field(default=0, ge=0)
    validation_status: CheckStatus | None = None
    execution_status: ExecutionStatus | None = None  # latest run
    execution_count: int = Field(default=0, ge=0)
    repair_count: int = Field(default=0, ge=0)
    passed_after_repair_count: int = Field(default=0, ge=0)
    manual_estimate_minutes: float | None = Field(default=None, gt=0)
    ai_migration_seconds: float | None = Field(default=None, ge=0)

    @computed_field
    @property
    def observed_time_saving_percent(self) -> float | None:
        if self.manual_estimate_minutes is None or self.ai_migration_seconds is None:
            return None
        return round((1 - self.ai_migration_seconds / (self.manual_estimate_minutes * 60)) * 100, 1)


class MigrationReport(CamelModel):
    migration_id: MigrationId
    test_case_id: Name
    target_framework: TargetFramework | None = None
    generated_at: datetime
    plan: MigrationPlan | None = None
    locator_resolutions: list[LocatorResolution] = Field(default_factory=list)
    validation: ValidationResult | None = None
    executions: list[ExecutionResult] = Field(default_factory=list)
    repairs: list[RepairResult] = Field(default_factory=list)
    traceability: list[TraceabilityRecord] = Field(default_factory=list)
    qtest_links: list[QTestLink] = Field(default_factory=list)
    metrics: MigrationMetrics = Field(default_factory=MigrationMetrics)
    demo_fault: DemoFault | None = None  # set when this target's run used DEMO MODE

    @computed_field
    @property
    def unresolved_locators(self) -> list[LocatorResolution]:
        return [resolution for resolution in self.locator_resolutions if not resolution.is_resolved]
