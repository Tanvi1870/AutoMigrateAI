from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from app.models.base import CamelModel
from app.models.enums import ExecutionStatus, TargetFramework


class ExecutionResult(CamelModel):
    status: ExecutionStatus
    target_framework: TargetFramework
    started_at: datetime
    duration_seconds: float = Field(ge=0)
    attempt: int = Field(default=1, ge=1)  # 2+ means a re-run, e.g. after an AI repair
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None  # None when the run was killed, e.g. on timeout
    logs: str = ""
    tests_total: int = Field(default=0, ge=0)
    tests_failed: int = Field(default=0, ge=0)
    failed_step: int | None = Field(default=None, ge=1)
    error_type: str | None = None
    error_message: str | None = None
    screenshots: list[str] = Field(default_factory=list)  # paths relative to the project directory
    dom_snapshots: list[str] = Field(default_factory=list)  # page HTML at failure, relative to the project
    artifacts_dir: str | None = None  # this run's artifacts, relative to the project directory

    @property
    def passed(self) -> bool:
        return self.status is ExecutionStatus.PASS

    @model_validator(mode="after")
    def _check_failure_details(self) -> Self:
        has_failure_details = any((self.failed_step, self.error_type, self.error_message))
        if self.passed and has_failure_details:
            raise ValueError("a passing execution must not carry failure details")
        if not self.passed and not (self.error_type or self.error_message):
            raise ValueError(f"a {self.status} execution must include error_type or error_message")
        if self.tests_failed > self.tests_total:
            raise ValueError("tests_failed cannot exceed tests_total")
        return self
