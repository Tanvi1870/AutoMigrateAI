from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from app.models.base import CamelModel
from app.models.canonical_test import CanonicalTestCase
from app.models.demo import DemoFault
from app.models.enums import TargetFramework
from app.models.execution_result import ExecutionResult
from app.models.fields import MigrationId
from app.models.migration_plan import MigrationPlan
from app.models.migration_result import MigrationResult
from app.models.repair import RepairResult
from app.models.traceability import QTestLink
from app.models.validation_result import ValidationResult


class MigrationRecord(CamelModel):
    """Aggregate state of one migration, persisted by the MigrationStore."""

    migration_id: MigrationId
    created_at: datetime
    test_case: CanonicalTestCase
    plans: dict[TargetFramework, MigrationPlan] = Field(default_factory=dict)
    results: dict[TargetFramework, MigrationResult] = Field(default_factory=dict)
    validations: dict[TargetFramework, ValidationResult] = Field(default_factory=dict)
    executions: dict[TargetFramework, list[ExecutionResult]] = Field(default_factory=dict)
    repairs: list[RepairResult] = Field(default_factory=list)
    qtest_links: list[QTestLink] = Field(default_factory=list)
    demo_faults: dict[TargetFramework, DemoFault] = Field(default_factory=dict)
    processing_seconds: float = Field(default=0.0, ge=0)  # measured analyze + generate time

    @model_validator(mode="after")
    def _check_integrity(self) -> Self:
        for target, item in [*self.plans.items(), *self.results.items()]:
            if item.target_framework != target:
                raise ValueError(f"{type(item).__name__} for '{item.target_framework}' stored under '{target}'")
            if item.migration_id != self.migration_id or item.test_case_id != self.test_case.test_case_id:
                raise ValueError(f"{type(item).__name__} belongs to a different migration or test case")
        for target, executions in self.executions.items():
            if any(execution.target_framework != target for execution in executions):
                raise ValueError(f"execution results stored under '{target}' target a different framework")
        for repair in self.repairs:
            if repair.migration_id != self.migration_id:
                raise ValueError("repair belongs to a different migration")
        return self
