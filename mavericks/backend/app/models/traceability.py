from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from app.models.base import CamelModel
from app.models.enums import ExecutionStatus, TargetFramework
from app.models.fields import Keyword, MigrationId, Name


class TraceabilityRecord(CamelModel):
    """Source step -> canonical step -> generated line -> execution -> test-management link."""

    source_test_case_id: Name
    source_step_ref: Name | None = None
    step_sequence: int = Field(ge=1)
    keyword: Keyword
    block_id: Name | None = None
    target_framework: TargetFramework | None = None
    generated_file: Name | None = None
    generated_line: int | None = Field(default=None, ge=1)
    execution_status: ExecutionStatus | None = None
    qtest_case_id: Name | None = Field(default=None, alias="qTestCaseId")

    @model_validator(mode="after")
    def _check_location(self) -> Self:
        if self.generated_line is not None and self.generated_file is None:
            raise ValueError("a generated line number requires a generated file")
        return self


class QTestCaseRef(CamelModel):
    """A qTest test case as returned by an adapter. `mock` is True for the simulated qTest."""

    qtest_case_id: Name = Field(alias="qTestCaseId")
    source_test_case_id: Name
    name: Name
    created: bool  # False when an existing case for the same source test was reused
    mock: bool


class AutomationLink(CamelModel):
    migration_id: MigrationId
    target_framework: TargetFramework
    script_path: Name  # workspace-relative, e.g. "playwright/MIG-001/tests/login_test.py"


class ExecutionUpdate(CamelModel):
    """One run's outcome and evidence, as sent to test management."""

    migration_id: MigrationId
    target_framework: TargetFramework
    attempt: int = Field(ge=1)
    status: ExecutionStatus
    started_at: datetime
    duration_seconds: float = Field(ge=0)
    failed_step: int | None = Field(default=None, ge=1)
    error_message: str | None = None
    logs: list[str] = Field(default_factory=list)  # workspace-relative paths
    screenshots: list[str] = Field(default_factory=list)


class QTestRunRef(CamelModel):
    qtest_run_id: Name = Field(alias="qTestRunId")
    qtest_case_id: Name = Field(alias="qTestCaseId")
    status: ExecutionStatus
    mock: bool


class QTestPublication(CamelModel):
    qtest_case_id: Name = Field(alias="qTestCaseId")
    published_runs: int = Field(ge=0)
    published_at: datetime
    mock: bool


class QTestLink(CamelModel):
    """ICAP test case <-> generated script <-> qTest test case <-> execution status <-> logs/screenshots."""

    source_test_case_id: Name
    generated_script: Name
    qtest_case_id: Name = Field(alias="qTestCaseId")
    execution_status: ExecutionStatus | None = None
    target_framework: TargetFramework | None = None
    qtest_run_id: Name | None = Field(default=None, alias="qTestRunId")
    attempt: int | None = Field(default=None, ge=1)
    executed_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    published: bool = False
    mock: bool = False  # the UI must label mock links "Mock qTest"


class TraceabilityReport(CamelModel):
    migration_id: MigrationId
    qtest_system: str  # "Mock qTest" or "qTest"
    links: list[QTestLink] = Field(default_factory=list)
    steps: list[TraceabilityRecord] = Field(default_factory=list)
