from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, computed_field, model_validator

from app.models.base import CamelModel
from app.models.canonical_test import Locator, TestStep
from app.models.enums import CheckStatus, LocatorStrategy, RepairChangeType, RepairStatus, TargetFramework
from app.models.execution_result import ExecutionResult
from app.models.fields import MigrationId, Name, Text

_APPLIED_STATUSES = frozenset({RepairStatus.APPLIED, RepairStatus.PASSED_AFTER_REPAIR, RepairStatus.FAILED})


class CandidateLocator(CamelModel):
    """A locator that matches an element in the failure-time DOM; repairs may only choose from these."""

    candidate_id: int = Field(ge=0)
    strategy: LocatorStrategy
    value: Name
    tag: str
    text: str | None = None
    score: float = Field(default=0.0, ge=0.0, le=1.0)  # deterministic similarity to the failed object

    @property
    def label(self) -> str:
        return f"{self.strategy}={self.value}"


class RepairContext(CamelModel):
    step: TestStep
    locator: Locator | None = None
    generated_code: str
    page_object_code: str = ""
    execution: ExecutionResult
    object_repository: dict[str, Locator] = Field(default_factory=dict)
    available_locators: list[CandidateLocator] = Field(default_factory=list)
    dom_snapshot: str | None = None

    @model_validator(mode="after")
    def _check_context(self) -> Self:
        if self.execution.passed:
            raise ValueError("repair context requires a failed execution")
        if self.locator is not None and self.locator.name != self.step.object_name:
            raise ValueError("locator does not belong to the failing step's object")
        return self


class RepairProposal(CamelModel):
    """A minimal, visible change to one failing step. Repairs never alter test intent."""

    step_sequence: int = Field(ge=1)
    change_type: RepairChangeType
    diagnosis: Text
    root_cause: Text
    proposed_change: Text
    old_value: str  # may be empty, e.g. when adding a wait that did not exist
    new_value: Name
    new_locator: Locator | None = None  # LOCATOR repairs keep it so verified repairs can be replayed
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: Text
    object_name: Name | None = None
    affected_steps: list[int] = Field(default_factory=list)  # every step that uses the changed object
    file: str | None = None  # the generated file the repair changes
    changed_files: list[str] = Field(default_factory=list)
    original_code: str = ""
    repaired_code: str = ""
    code_diff: str = ""
    # "llm": a real model chose the candidate; "mock": the offline rule-based MockLLMProvider did;
    # "heuristic": no model was used (similarity ranking only).
    analysis_source: Literal["llm", "mock", "heuristic"] = "heuristic"

    @model_validator(mode="after")
    def _check_change(self) -> Self:
        if self.new_value == self.old_value:
            raise ValueError("a repair proposal must change the value")
        return self


class RepairStage(StrEnum):
    ORIGINAL = "ORIGINAL"
    FAILURE = "FAILURE"
    AI_ANALYSIS = "AI_ANALYSIS"
    REPAIR = "REPAIR"
    VALIDATION = "VALIDATION"
    RERUN = "RERUN"
    RESULT = "RESULT"


class StageStatus(StrEnum):
    DONE = "DONE"
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    PENDING = "PENDING"
    SKIPPED = "SKIPPED"


class TimelineEntry(CamelModel):
    stage: RepairStage
    title: str
    status: StageStatus
    details: list[str] = Field(default_factory=list)


class RepairResult(CamelModel):
    """Lifecycle of one AI repair: proposed -> applied -> verified/failed by a re-run (or rejected)."""

    migration_id: MigrationId
    target_framework: TargetFramework
    proposal: RepairProposal
    status: RepairStatus = RepairStatus.PROPOSED
    validation_status: CheckStatus | None = None
    validation_summary: str | None = None
    re_execution: ExecutionResult | None = None
    original_step: TestStep | None = None
    original_locator: Locator | None = None
    original_execution: ExecutionResult | None = None
    rolled_back: bool = False  # a failed re-run restores the original files
    message: str | None = None
    created_at: datetime

    @property
    def applied(self) -> bool:
        return self.status in _APPLIED_STATUSES

    @model_validator(mode="after")
    def _check_lifecycle(self) -> Self:
        if not self.applied and self.re_execution:
            raise ValueError(f"a {self.status} repair cannot have a re-execution result")
        if self.status is RepairStatus.PROPOSED and self.validation_status:
            raise ValueError("a PROPOSED repair cannot have validation results")
        if self.status is RepairStatus.REJECTED and self.validation_status not in (None, CheckStatus.FAIL):
            raise ValueError("a REJECTED repair can only carry a failed validation")
        if self.status in (RepairStatus.PASSED_AFTER_REPAIR, RepairStatus.FAILED) and self.re_execution is None:
            raise ValueError(f"a {self.status} repair requires a re-execution result")
        if self.re_execution is not None:
            if self.re_execution.target_framework != self.target_framework:
                raise ValueError("re-execution target does not match the repair target")
            if self.status is RepairStatus.PASSED_AFTER_REPAIR and not self.re_execution.passed:
                raise ValueError("a PASSED_AFTER_REPAIR repair requires a passing re-execution")
            if self.status is RepairStatus.FAILED and self.re_execution.passed:
                raise ValueError("a FAILED repair cannot have a passing re-execution")
        return self

    @computed_field
    @property
    def outcome(self) -> str:
        """Before/after comparison, e.g. 'FAIL at step 4 -> PASS'."""
        before = _describe(self.original_execution) if self.original_execution else "FAIL"
        after = _describe(self.re_execution) if self.re_execution else str(self.status)
        return f"{before} -> {after}"

    @computed_field
    @property
    def timeline(self) -> list[TimelineEntry]:
        """Original -> Failure -> AI Analysis -> Repair -> Validation -> Re-run -> PASS/FAIL, for the UI."""
        p, step, execution = self.proposal, self.original_step, self.original_execution
        original = [f"Step {p.step_sequence}" + (f": {step.keyword} {step.object_name or ''}".rstrip() if step else "")]
        if self.original_locator:
            original.append(f"Locator {self.original_locator.strategy}={self.original_locator.value}")
        failure = [f"{execution.status} at step {execution.failed_step}"] if execution else []
        if execution and (execution.error_type or execution.error_message):
            failure.append(f"{execution.error_type}: {(execution.error_message or '').splitlines()[0][:300]}")
        repair = [p.proposed_change, f"{p.old_value or '(none)'} -> {p.new_value}"]
        if p.affected_steps:
            repair.append(f"Affects steps {', '.join(map(str, p.affected_steps))}")
        if p.changed_files or p.file:
            repair.append(f"Changes only {', '.join(p.changed_files or [p.file or ''])}")
        return [
            TimelineEntry(stage=RepairStage.ORIGINAL, title="Original", status=StageStatus.DONE, details=original),
            TimelineEntry(
                stage=RepairStage.FAILURE,
                title="Failure",
                status=StageStatus.FAIL if execution else StageStatus.SKIPPED,
                details=failure,
            ),
            TimelineEntry(
                stage=RepairStage.AI_ANALYSIS,
                title="AI Analysis",
                status=StageStatus.DONE,
                details=[
                    p.diagnosis,
                    f"Root cause: {p.root_cause}",
                    f"Confidence {p.confidence:.0%} ({p.analysis_source})",
                ],
            ),
            TimelineEntry(stage=RepairStage.REPAIR, title="Repair", status=StageStatus.DONE, details=repair),
            TimelineEntry(
                stage=RepairStage.VALIDATION,
                title="Validation",
                status=_stage_status(self.validation_status),
                details=[self.validation_summary] if self.validation_summary else [],
            ),
            TimelineEntry(
                stage=RepairStage.RERUN,
                title="Re-run",
                status=_execution_stage(self.re_execution, self.status),
                details=[_describe(self.re_execution)] if self.re_execution else [],
            ),
            TimelineEntry(
                stage=RepairStage.RESULT,
                title="PASS" if self.status is RepairStatus.PASSED_AFTER_REPAIR else "FAIL" if self.re_execution else str(self.status),
                status=_result_stage(self.status),
                details=[self.outcome] + ([self.message] if self.message else []),
            ),
        ]


def _describe(execution: ExecutionResult) -> str:
    if execution.passed:
        return "PASS"
    return f"{execution.status} at step {execution.failed_step}" if execution.failed_step else str(execution.status)


def _stage_status(status: CheckStatus | None) -> StageStatus:
    if status is None:
        return StageStatus.PENDING
    return {CheckStatus.PASS: StageStatus.PASS, CheckStatus.WARN: StageStatus.WARNING}.get(status, StageStatus.FAIL)


def _execution_stage(execution: ExecutionResult | None, status: RepairStatus) -> StageStatus:
    if execution is not None:
        return StageStatus.PASS if execution.passed else StageStatus.FAIL
    return StageStatus.SKIPPED if status is RepairStatus.REJECTED else StageStatus.PENDING


def _result_stage(status: RepairStatus) -> StageStatus:
    return {
        RepairStatus.PASSED_AFTER_REPAIR: StageStatus.PASS,
        RepairStatus.FAILED: StageStatus.FAIL,
        RepairStatus.REJECTED: StageStatus.FAIL,
    }.get(status, StageStatus.PENDING)
