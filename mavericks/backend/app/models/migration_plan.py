from enum import StrEnum
from typing import Annotated, ClassVar, Self

from pydantic import Field, StringConstraints, computed_field, model_validator

from app.models.base import CamelModel, StrictCamelModel
from app.models.capabilities import SUPPORTED_TARGETS
from app.models.enums import ApplicationType, PlanStatus, TargetFramework
from app.models.fields import MigrationId, Name

AdvisoryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
AdvisoryNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class AITestAnalysis(StrictCamelModel):
    """The only shape the LLM may return for test analysis.

    Advisory text for people: no generator reads these fields, so LLM output can never become test code.
    """

    summary: AdvisoryText
    intent: AdvisoryText
    risks: list[AdvisoryNote] = Field(default_factory=list, max_length=20)
    recommendations: list[AdvisoryNote] = Field(default_factory=list, max_length=20)


class TestAnalysis(CamelModel):
    __test__: ClassVar[bool] = False

    application_type: ApplicationType
    steps_detected: int = Field(ge=0)
    keywords_detected: list[str] = Field(default_factory=list)
    object_references: list[str] = Field(default_factory=list)
    data_parameters: list[str] = Field(default_factory=list)
    reusable_blocks: int = Field(default=0, ge=0)
    has_assertion: bool = False
    ai_analysis: AITestAnalysis | None = None
    warnings: list[str] = Field(default_factory=list)


class UnsupportedItemKind(StrEnum):
    TARGET = "TARGET"
    KEYWORD = "KEYWORD"
    FUTURE_ADAPTER = "FUTURE_ADAPTER"
    LOCATOR = "LOCATOR"
    VARIABLE = "VARIABLE"


class UnsupportedItem(CamelModel):
    kind: UnsupportedItemKind
    name: str
    reason: str


class MigrationPlan(CamelModel):
    """Status is derived from the blocking findings, so a plan can never claim READY while blocked."""

    migration_id: MigrationId
    test_case_id: Name
    application_type: ApplicationType
    target_framework: TargetFramework
    steps_detected: int = Field(ge=0)
    keywords_detected: int = Field(ge=0)
    keywords_mapped: int = Field(ge=0)
    locators_resolved: int = Field(ge=0)
    data_parameters: int = Field(ge=0)
    reusable_blocks: int = Field(ge=0)
    unsupported_keywords: list[str] = Field(default_factory=list)
    future_adapter_keywords: list[str] = Field(default_factory=list)
    missing_locators: list[str] = Field(default_factory=list)
    unresolved_variables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    summary: str | None = None  # optional LLM-written narrative
    ai_analysis: AITestAnalysis | None = None
    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        if self.keywords_mapped > self.keywords_detected:
            raise ValueError("keywords_mapped cannot exceed keywords_detected")
        return self

    @computed_field
    @property
    def blocking_reasons(self) -> list[str]:
        reasons = list(self.errors)
        if self.target_framework not in SUPPORTED_TARGETS[self.application_type]:
            reasons.append(f"Target '{self.target_framework}' does not support {self.application_type} applications")
        reasons += [f"Unsupported keyword: {k}" for k in self.unsupported_keywords]
        reasons += [f"Keyword requires a future adapter: {k}" for k in self.future_adapter_keywords]
        reasons += [f"Missing locator: {name}" for name in self.missing_locators]
        reasons += [f"Unresolved test data variable: {name}" for name in self.unresolved_variables]
        return reasons

    @computed_field
    @property
    def unsupported_items(self) -> list[UnsupportedItem]:
        items = []
        if self.target_framework not in SUPPORTED_TARGETS[self.application_type]:
            items.append(
                UnsupportedItem(
                    kind=UnsupportedItemKind.TARGET,
                    name=self.target_framework,
                    reason=f"No {self.target_framework} support for {self.application_type} applications",
                )
            )
        findings = [
            (UnsupportedItemKind.KEYWORD, self.unsupported_keywords, f"No {self.target_framework} mapping"),
            (UnsupportedItemKind.FUTURE_ADAPTER, self.future_adapter_keywords, "Needs a future adapter"),
            (UnsupportedItemKind.LOCATOR, self.missing_locators, "No usable locator"),
            (UnsupportedItemKind.VARIABLE, self.unresolved_variables, "No value in the test data"),
        ]
        items += [UnsupportedItem(kind=kind, name=name, reason=reason) for kind, names, reason in findings for name in names]
        return items

    @computed_field
    @property
    def status(self) -> PlanStatus:
        return PlanStatus.BLOCKED if self.blocking_reasons else PlanStatus.READY
