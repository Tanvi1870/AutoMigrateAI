"""Canonical Test Model: the source-agnostic contract between parsers and generators."""

import re
from collections.abc import Sequence
from typing import Any, ClassVar, Self

from pydantic import ConfigDict, Field, model_validator

from app.models.base import StrictCamelModel
from app.models.capabilities import LOCATOR_STRATEGIES
from app.models.enums import ApplicationType, LocatorSource, LocatorStrategy
from app.models.fields import VARIABLE_REFERENCE, Keyword, Name, VariableName

MASKED_VALUE = "******"
_AI_SOURCES = frozenset({LocatorSource.AI_INFERRED, LocatorSource.AI_REPAIRED})
_SCREEN_POSITION = re.compile(r"[1-9]\d*,[1-9]\d*")
_SENSITIVE_MARKERS = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "API_KEY", "APIKEY", "CREDENTIAL")
_SENSITIVE_TOKENS = frozenset({"PWD", "PIN"})


def _require_increasing(steps: Sequence["TestStep"], owner: str) -> None:
    sequences = [step.sequence for step in steps]
    if any(later <= earlier for earlier, later in zip(sequences, sequences[1:])):
        raise ValueError(f"{owner} step sequences must be strictly increasing, got {sequences}")


def is_sensitive_name(name: str) -> bool:
    """Name-based detection of secrets (variable or object names such as PASSWORD, apiToken, user_pin).

    The single rule used for masking test data, LLM prompts and generated data files.
    """
    upper = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).upper()
    return any(marker in upper.replace("_", "") for marker in _SENSITIVE_MARKERS) or not _SENSITIVE_TOKENS.isdisjoint(
        re.split(r"[^A-Z0-9]+", upper)
    )


class SourceInfo(StrictCamelModel):
    """Provenance of a canonical test, e.g. system='ICAP', format='representative-icap-export/1.0'."""

    system: Name
    format: Name
    reference: Name | None = None


class Locator(StrictCamelModel):
    name: Name
    strategy: LocatorStrategy
    value: Name
    source: LocatorSource = LocatorSource.OBJECT_REPOSITORY
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def ai_inferred(self) -> bool:
        return self.source in _AI_SOURCES

    @property
    def application_types(self) -> frozenset[ApplicationType]:
        return frozenset(app for app, strategies in LOCATOR_STRATEGIES.items() if self.strategy in strategies)

    @model_validator(mode="after")
    def _check_value_and_source(self) -> Self:
        if self.strategy is LocatorStrategy.ID and any(ch.isspace() for ch in self.value):
            raise ValueError("id locator values must not contain whitespace")
        if self.strategy is LocatorStrategy.XPATH and not self.value.startswith(("/", "(", ".")):
            raise ValueError("xpath locator values must start with '/', '(' or '.'")
        if self.strategy is LocatorStrategy.SCREEN_POSITION and not _SCREEN_POSITION.fullmatch(self.value):
            raise ValueError("screen-position locator values must be 'row,column' with 1-based integers")
        if self.ai_inferred and self.confidence is None:
            raise ValueError("AI-sourced locators must state a confidence")
        if not self.ai_inferred and self.confidence is not None:
            raise ValueError("confidence is only allowed for AI-sourced locators")
        return self


class TestStep(StrictCamelModel):
    __test__: ClassVar[bool] = False  # stop pytest collecting this as a test class

    sequence: int = Field(ge=1)
    keyword: Keyword
    object_name: Name | None = Field(default=None, alias="object")
    value: str | None = None
    expected: str | None = None
    block_id: Name | None = None  # set when the step was expanded from a reusable block
    source_step_ref: Name | None = None  # original step reference in the source asset
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def variable_references(self) -> set[str]:
        return {name for text in (self.value, self.expected) if text for name in VARIABLE_REFERENCE.findall(text)}


class ReusableBlock(StrictCamelModel):
    block_id: Name
    name: Name
    steps: list[TestStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_steps(self) -> Self:
        _require_increasing(self.steps, f"reusable block {self.block_id}")
        return self


class TestData(StrictCamelModel):
    __test__: ClassVar[bool] = False

    model_config = ConfigDict(coerce_numbers_to_str=True)

    variables: dict[VariableName, str] = Field(default_factory=dict)
    sensitive_keys: set[VariableName] = Field(default_factory=set)  # in addition to name-based detection

    @model_validator(mode="before")
    @classmethod
    def _accept_flat_mapping(cls, data: Any) -> Any:
        if isinstance(data, dict) and not isinstance(data.get("variables"), dict):
            return {"variables": data}
        return data

    @model_validator(mode="after")
    def _check_sensitive_keys(self) -> Self:
        unknown = self.sensitive_keys - self.variables.keys()
        if unknown:
            raise ValueError(f"sensitive keys are not defined as variables: {sorted(unknown)}")
        return self

    def is_sensitive(self, name: str) -> bool:
        return name in self.sensitive_keys or is_sensitive_name(name)

    def masked(self) -> dict[str, str]:
        return {name: MASKED_VALUE if self.is_sensitive(name) else value for name, value in self.variables.items()}


class CanonicalTestCase(StrictCamelModel):
    test_case_id: Name
    name: Name
    description: str | None = None
    application_type: ApplicationType
    steps: list[TestStep] = Field(min_length=1)
    test_data: TestData = Field(default_factory=TestData)
    reusable_blocks: list[ReusableBlock] = Field(default_factory=list)
    object_repository: dict[Name, Locator] = Field(default_factory=dict)
    source: SourceInfo | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        _require_increasing(self.steps, "test case")

        block_ids = [block.block_id for block in self.reusable_blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("reusable block IDs must be unique")
        unknown_blocks = sorted({step.block_id for step in self.steps if step.block_id} - set(block_ids))
        if unknown_blocks:
            raise ValueError(f"steps reference undefined reusable blocks: {unknown_blocks}")

        for key, locator in self.object_repository.items():
            if key != locator.name:
                raise ValueError(f"object repository key {key!r} does not match locator name {locator.name!r}")
            if self.application_type not in locator.application_types:
                raise ValueError(
                    f"locator {key!r} uses strategy '{locator.strategy}', "
                    f"which is not valid for {self.application_type} applications"
                )
        return self

    def referenced_variables(self) -> list[str]:
        return sorted(set().union(*(step.variable_references for step in self.steps)))

    def unresolved_variables(self) -> list[str]:
        return [name for name in self.referenced_variables() if name not in self.test_data.variables]

    def missing_objects(self) -> list[str]:
        """Referenced objects with no locator; reported by planning rather than rejected here."""
        missing = (s.object_name for s in self.steps if s.object_name and s.object_name not in self.object_repository)
        return list(dict.fromkeys(missing))
