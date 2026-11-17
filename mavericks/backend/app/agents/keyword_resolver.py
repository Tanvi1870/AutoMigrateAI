"""Maps keywords to framework code using the keyword knowledge base only.

This module is the single source of Playwright/Selenium code. The LLM is never consulted here, so it
cannot invent framework APIs: unsupported keywords yield a structured result, never code.
"""

import keyword as python_keyword
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.knowledge.keyword_definitions import (
    PLACEHOLDER_PATTERN,
    KeywordDefinition,
    KeywordParameter,
    RuleType,
    ValidationRule,
)
from app.knowledge.keyword_repository import KeywordRepository
from app.models.canonical_test import CanonicalTestCase, TestStep
from app.models.enums import ApplicationType, TargetFramework
from app.models.fields import VARIABLE_REFERENCE
from app.models.keyword_resolution import (
    KeywordResolution,
    ParameterIssue,
    ParameterIssueCode,
    ResolutionStatus,
    UnsupportedReason,
)

SELENIUM_BY_CONSTANTS = frozenset(
    {"ID", "NAME", "XPATH", "CSS_SELECTOR", "CLASS_NAME", "TAG_NAME", "LINK_TEXT", "PARTIAL_LINK_TEXT"}
)
# Names the generated test code already binds; a runtime variable must not shadow them.
RESERVED_VARIABLE_NAMES = frozenset({"page", "driver", "expect", "By", "Select", "WebDriverWait", "EC", "test_data"})
_INTEGER = re.compile(r"[0-9]+")
_DOTTED_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


class UnsupportedKeywordError(LookupError):
    """Raised when code is requested for a step whose resolution is not RESOLVED."""

    def __init__(self, resolution: KeywordResolution) -> None:
        super().__init__(resolution.message or f"{resolution.keyword}: {resolution.status}")
        self.resolution = resolution


class TemplateRenderError(ValueError):
    """The render context does not provide what the keyword template needs."""


@dataclass(frozen=True)
class RenderContext:
    """Target-specific locator parts produced by the LocatorResolver."""

    locator: str | None = None  # Playwright selector
    by: str | None = None  # Selenium By constant name, e.g. "ID"
    locator_value: str | None = None  # Selenium locator value
    # Generator-owned dotted names emitted verbatim instead of the literal, e.g. "LoginPage.USERNAME".
    locator_ref: str | None = None
    locator_value_ref: str | None = None
    # Callable that resolves ${VAR} references at runtime, e.g. "test_data.resolve".
    data_resolver: str | None = None


class KeywordResolver:
    def __init__(self, repository: KeywordRepository) -> None:
        self._repository = repository

    def resolve(self, keyword: str, target: TargetFramework) -> KeywordResolution:
        """Keyword-level resolution, without step parameter checks."""
        definition = self._repository.get(keyword)
        if definition is None:
            return self._not_in_knowledge_base(keyword, target)
        mapping = definition.targets.get(target)
        if mapping is None:
            return self._unsupported(
                keyword,
                target,
                UnsupportedReason.TARGET_NOT_MAPPED,
                f"Keyword {keyword!r} has no {target} mapping in the knowledge base",
                application_type=definition.application_type,
            )
        return KeywordResolution(
            keyword=keyword,
            target_framework=target,
            status=ResolutionStatus.RESOLVED,
            application_type=definition.application_type,
            description=definition.description,
            template=mapping.python,
            imports=list(mapping.imports),
            assumption=definition.assumption,
        )

    def resolve_step(
        self, step: TestStep, target: TargetFramework, application_type: ApplicationType | None = None
    ) -> KeywordResolution:
        """Resolution of one canonical step, including the KB's parameter and validation rules."""
        resolution = self.resolve(step.keyword, target)
        definition = self._repository.get(step.keyword)
        if not resolution.is_resolved or definition is None:
            return resolution.model_copy(update={"step_sequence": step.sequence})

        if application_type is not None and definition.application_type is not application_type:
            return self._unsupported(
                step.keyword,
                target,
                UnsupportedReason.APPLICATION_TYPE_MISMATCH,
                f"Keyword {step.keyword!r} is a {definition.application_type} keyword "
                f"but the test case is {application_type}",
                application_type=definition.application_type,
                step_sequence=step.sequence,
            )

        issues = _check_parameters(definition, step)
        if issues:
            return KeywordResolution(
                keyword=step.keyword,
                target_framework=target,
                status=ResolutionStatus.INVALID,
                step_sequence=step.sequence,
                application_type=definition.application_type,
                description=definition.description,
                assumption=definition.assumption,
                issues=issues,
            )
        return resolution.model_copy(
            update={"step_sequence": step.sequence, "warnings": _unused_parameters(definition, step)}
        )

    def resolve_test_case(self, test_case: CanonicalTestCase, target: TargetFramework) -> list[KeywordResolution]:
        return [self.resolve_step(step, target, test_case.application_type) for step in test_case.steps]

    def render(
        self,
        step: TestStep,
        target: TargetFramework,
        context: RenderContext,
        application_type: ApplicationType | None = None,
    ) -> str:
        """Render a step's KB template. Raises UnsupportedKeywordError instead of ever guessing code."""
        resolution = self.resolve_step(step, target, application_type)
        definition = self._repository.get(step.keyword)
        if not resolution.is_resolved or definition is None or resolution.template is None:
            raise UnsupportedKeywordError(resolution)
        bindings = {
            name: _binding(name, definition, step, context)
            for name in PLACEHOLDER_PATTERN.findall(resolution.template)
        }
        return PLACEHOLDER_PATTERN.sub(lambda match: bindings[match.group(1)], resolution.template)

    def _not_in_knowledge_base(self, keyword: str, target: TargetFramework) -> KeywordResolution:
        adapter = self._repository.future_adapter(keyword)
        if adapter is not None:
            prefix, definition = adapter
            return self._unsupported(
                keyword,
                target,
                UnsupportedReason.FUTURE_ADAPTER,
                f"Keyword {keyword!r} ({prefix}*) needs the future {definition.application_type} adapter; "
                f"it is not converted to {target}",
                application_type=definition.application_type,
            )
        return self._unsupported(
            keyword,
            target,
            UnsupportedReason.UNKNOWN_KEYWORD,
            f"Keyword {keyword!r} is not in the keyword knowledge base",
            suggestions=self._repository.suggest(keyword),
        )

    @staticmethod
    def _unsupported(
        keyword: str,
        target: TargetFramework,
        reason: UnsupportedReason,
        message: str,
        *,
        application_type: ApplicationType | None = None,
        suggestions: list[str] | None = None,
        step_sequence: int | None = None,
    ) -> KeywordResolution:
        return KeywordResolution(
            keyword=keyword,
            target_framework=target,
            status=ResolutionStatus.UNSUPPORTED,
            step_sequence=step_sequence,
            application_type=application_type,
            reason=reason,
            message=message,
            suggestions=suggestions or [],
        )


def _parameter_value(step: TestStep, parameter: KeywordParameter) -> str | None:
    return {
        KeywordParameter.OBJECT: step.object_name,
        KeywordParameter.VALUE: step.value,
        KeywordParameter.EXPECTED: step.expected,
    }[parameter]


def _check_parameters(definition: KeywordDefinition, step: TestStep) -> list[ParameterIssue]:
    issues = [
        ParameterIssue(
            parameter=parameter,
            code=ParameterIssueCode.MISSING_REQUIRED,
            message=f"Parameter '{parameter}' is required by {step.keyword}",
        )
        for parameter in definition.required
        if _parameter_value(step, parameter) is None
    ]
    for rule in definition.validation_rules:
        value = _parameter_value(step, rule.parameter)
        if value is not None and not _satisfies(rule, value):
            issues.append(
                ParameterIssue(
                    parameter=rule.parameter,
                    code=ParameterIssueCode.RULE_VIOLATION,
                    message=f"{rule.message or f'{rule.parameter} violates {rule.rule}'} (got {value!r})",
                )
            )
    return issues


def _satisfies(rule: ValidationRule, value: str) -> bool:
    match rule.rule:
        case RuleType.NOT_EMPTY:
            return bool(value.strip())
        case RuleType.URL:
            if value.startswith("${"):
                return True
            parsed = urlparse(value)
            return parsed.scheme in ("http", "https") and bool(parsed.netloc) and not any(c.isspace() for c in value)
        case RuleType.PYTHON_IDENTIFIER:
            return _is_safe_identifier(value)
        case RuleType.INTEGER_RANGE:
            return (
                bool(_INTEGER.fullmatch(value))
                and rule.min is not None
                and rule.max is not None
                and rule.min <= int(value) <= rule.max
            )
    return False


def _is_safe_identifier(value: str) -> bool:
    return (
        value.isidentifier()
        and value.isascii()
        and not python_keyword.iskeyword(value)
        and not value.startswith("__")
        and value not in RESERVED_VARIABLE_NAMES
    )


def _unused_parameters(definition: KeywordDefinition, step: TestStep) -> list[str]:
    declared = {*definition.required, *definition.optional}
    return [
        f"Parameter '{parameter}' is not used by {step.keyword} and will be ignored"
        for parameter in KeywordParameter
        if parameter not in declared and _parameter_value(step, parameter) is not None
    ]


def _binding(name: str, definition: KeywordDefinition, step: TestStep, context: RenderContext) -> str:
    """Python source for one placeholder. Strings become repr() literals so data can never become code."""
    value = step.value if step.value is not None else definition.default_for(KeywordParameter.VALUE)
    match name:
        case "locator":
            if context.locator_ref is not None:
                return _dotted_name(context.locator_ref)
            return repr(_require(context.locator, "locator"))
        case "locator_value":
            if context.locator_value_ref is not None:
                return _dotted_name(context.locator_value_ref)
            return repr(_require(context.locator_value, "locator_value"))
        case "by":
            by = _require(context.by, "by")
            if by not in SELENIUM_BY_CONSTANTS:
                raise TemplateRenderError(f"{by!r} is not a Selenium By constant")
            return by
        case "value":
            return _text(_require(value, "value"), context)
        case "expected":
            return _text(_require(step.expected, "expected"), context)
        case "variable":
            variable = _require(value, "value")
            if not _is_safe_identifier(variable):
                raise TemplateRenderError(f"{variable!r} is not a safe variable name")
            return variable
        case "timeout_ms" | "timeout_s":
            timeout = _require(value, "value")
            if not _INTEGER.fullmatch(timeout):
                raise TemplateRenderError(f"timeout {timeout!r} is not a whole number of milliseconds")
            return str(int(timeout)) if name == "timeout_ms" else f"{int(timeout) / 1000:g}"
    raise TemplateRenderError(f"unknown template placeholder {{{name}}}")


def _require(value: str | None, name: str) -> str:
    if value is None:
        raise TemplateRenderError(f"template needs '{name}' but none was provided")
    return value


def _dotted_name(name: str) -> str:
    if not _DOTTED_NAME.fullmatch(name) or any(python_keyword.iskeyword(part) for part in name.split(".")):
        raise TemplateRenderError(f"{name!r} is not a safe dotted name")
    return name


def _text(text: str, context: RenderContext) -> str:
    if context.data_resolver is not None and VARIABLE_REFERENCE.search(text):
        return f"{_dotted_name(context.data_resolver)}({text!r})"
    return repr(text)
