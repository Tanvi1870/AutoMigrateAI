from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.models.base import CamelModel
from app.models.enums import ApplicationType, TargetFramework
from app.models.fields import Text


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"  # keyword is mapped, but the step's parameters break its rules


class UnsupportedReason(StrEnum):
    UNKNOWN_KEYWORD = "UNKNOWN_KEYWORD"
    FUTURE_ADAPTER = "FUTURE_ADAPTER"
    TARGET_NOT_MAPPED = "TARGET_NOT_MAPPED"
    APPLICATION_TYPE_MISMATCH = "APPLICATION_TYPE_MISMATCH"


class ParameterIssueCode(StrEnum):
    MISSING_REQUIRED = "MISSING_REQUIRED"
    RULE_VIOLATION = "RULE_VIOLATION"


class ParameterIssue(CamelModel):
    parameter: str
    code: ParameterIssueCode
    message: Text


class KeywordResolution(CamelModel):
    """Outcome of mapping one keyword to one target. Only RESOLVED results carry a template."""

    keyword: str
    target_framework: TargetFramework
    status: ResolutionStatus
    step_sequence: int | None = Field(default=None, ge=1)
    application_type: ApplicationType | None = None
    description: str | None = None
    template: str | None = None
    imports: list[str] = Field(default_factory=list)
    assumption: str | None = None
    reason: UnsupportedReason | None = None
    message: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    issues: list[ParameterIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return self.status is ResolutionStatus.RESOLVED

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.is_resolved:
            if self.template is None or self.reason is not None or self.issues:
                raise ValueError("a RESOLVED result needs a template and no reason or issues")
        elif self.template is not None or self.imports:
            raise ValueError(f"a {self.status} result must not carry generated code")
        if self.status is ResolutionStatus.UNSUPPORTED and (self.reason is None or not self.message):
            raise ValueError("an UNSUPPORTED result needs a reason and a message")
        if self.status is ResolutionStatus.INVALID and (not self.issues or self.reason is not None):
            raise ValueError("an INVALID result needs parameter issues and no unsupported reason")
        return self
