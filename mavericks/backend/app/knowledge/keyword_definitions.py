"""Typed schema for keyword_kb.json; a malformed knowledge base fails at load time, not at generation."""

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.models.base import StrictCamelModel
from app.models.enums import ApplicationType, TargetFramework
from app.models.fields import Keyword, Name, Text


class KeywordStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    ASSUMED = "ASSUMED"  # mapped, but ICAP semantics are unconfirmed
    FUTURE_ADAPTER = "FUTURE_ADAPTER"  # recognised, not convertible yet
    UNKNOWN = "UNKNOWN"


class KeywordParameter(StrEnum):
    """Canonical TestStep fields a keyword can consume."""

    OBJECT = "object"
    VALUE = "value"
    EXPECTED = "expected"


class RuleType(StrEnum):
    NOT_EMPTY = "notEmpty"
    URL = "url"
    PYTHON_IDENTIFIER = "pythonIdentifier"
    INTEGER_RANGE = "integerRange"


PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_]+)\}")

# Which step parameter each template placeholder draws from.
PLACEHOLDER_PARAMETERS: dict[str, KeywordParameter] = {
    "locator": KeywordParameter.OBJECT,
    "by": KeywordParameter.OBJECT,
    "locator_value": KeywordParameter.OBJECT,
    "value": KeywordParameter.VALUE,
    "expected": KeywordParameter.EXPECTED,
    "variable": KeywordParameter.VALUE,
    "timeout_ms": KeywordParameter.VALUE,
    "timeout_s": KeywordParameter.VALUE,
}

TARGET_PLACEHOLDERS: dict[TargetFramework, frozenset[str]] = {
    TargetFramework.PLAYWRIGHT: frozenset({"locator", "value", "expected", "variable", "timeout_ms"}),
    TargetFramework.SELENIUM: frozenset({"by", "locator_value", "value", "expected", "variable", "timeout_s"}),
}

_IDENT = r"[A-Za-z_]\w*"
_IMPORT_LINE = re.compile(
    rf"from {_IDENT}(\.{_IDENT})* import {_IDENT}( as {_IDENT})?(, {_IDENT}( as {_IDENT})?)*"
    rf"|import {_IDENT}(\.{_IDENT})*( as {_IDENT})?"
)


class ParameterSpec(StrictCamelModel):
    name: KeywordParameter
    description: Text
    default: str | None = None


class ValidationRule(StrictCamelModel):
    parameter: KeywordParameter
    rule: RuleType
    min: int | None = None
    max: int | None = None
    message: Text | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.rule is RuleType.INTEGER_RANGE:
            if self.min is None or self.max is None or self.min > self.max:
                raise ValueError("integerRange rules need 'min' <= 'max'")
        elif self.min is not None or self.max is not None:
            raise ValueError(f"'{self.rule}' rules do not take 'min'/'max'")
        return self


class TargetMapping(StrictCamelModel):
    python: Text
    imports: list[str] = Field(default_factory=list)
    note: str | None = None

    @property
    def placeholders(self) -> set[str]:
        return set(PLACEHOLDER_PATTERN.findall(self.python))

    @model_validator(mode="after")
    def _check_imports(self) -> Self:
        invalid = [line for line in self.imports if not _IMPORT_LINE.fullmatch(line)]
        if invalid:
            raise ValueError(f"invalid import statement(s): {invalid}")
        return self


class KeywordDefinition(StrictCamelModel):
    description: Text
    application_type: ApplicationType
    status: KeywordStatus
    assumption: Text | None = None
    required_parameters: list[ParameterSpec] = Field(default_factory=list)
    optional_parameters: list[ParameterSpec] = Field(default_factory=list)
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    targets: dict[TargetFramework, TargetMapping] = Field(min_length=1)

    @property
    def required(self) -> tuple[KeywordParameter, ...]:
        return tuple(p.name for p in self.required_parameters)

    @property
    def optional(self) -> tuple[KeywordParameter, ...]:
        return tuple(p.name for p in self.optional_parameters)

    def default_for(self, parameter: KeywordParameter) -> str | None:
        return next((p.default for p in self.optional_parameters if p.name is parameter), None)

    @model_validator(mode="after")
    def _check_definition(self) -> Self:
        if self.status not in (KeywordStatus.SUPPORTED, KeywordStatus.ASSUMED):
            raise ValueError("keyword status must be SUPPORTED or ASSUMED")
        if (self.status is KeywordStatus.ASSUMED) != (self.assumption is not None):
            raise ValueError("an 'assumption' is required for ASSUMED keywords and only allowed for them")

        declared = [*self.required, *self.optional]
        if len(declared) != len(set(declared)):
            raise ValueError("a parameter cannot be declared twice")
        if any(p.default is not None for p in self.required_parameters):
            raise ValueError("required parameters cannot have a default")
        for rule in self.validation_rules:
            if rule.parameter not in declared:
                raise ValueError(f"validation rule targets undeclared parameter '{rule.parameter}'")

        for target, mapping in self.targets.items():
            unknown = mapping.placeholders - TARGET_PLACEHOLDERS[target]
            if unknown:
                raise ValueError(f"{target} template uses unknown placeholder(s) {sorted(unknown)}")
            undeclared = {PLACEHOLDER_PARAMETERS[p] for p in mapping.placeholders} - set(declared)
            if undeclared:
                raise ValueError(f"{target} template uses undeclared parameter(s) {sorted(undeclared)}")
        return self


class FutureAdapterDefinition(StrictCamelModel):
    application_type: ApplicationType
    status: Literal["FUTURE_ADAPTER"]
    note: Text


class KeywordKnowledgeBase(StrictCamelModel):
    version: Name
    placeholder_convention: str | None = None
    keywords: dict[Keyword, KeywordDefinition] = Field(min_length=1)
    future_adapters: dict[Name, FutureAdapterDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_prefixes(self) -> Self:
        clashes = [k for k in self.keywords if any(k.startswith(prefix) for prefix in self.future_adapters)]
        if clashes:
            raise ValueError(f"keywords {clashes} collide with future-adapter prefixes")
        return self
