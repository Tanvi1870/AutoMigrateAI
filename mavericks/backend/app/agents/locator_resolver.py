"""Resolves step object names to locators using the object repository and locator_rules.json only.

The LLM is never consulted here: an object without a repository entry yields an UNRESOLVED result
that is surfaced in the migration plan/report, never a guessed locator.
"""

import difflib
from collections.abc import Iterable, Mapping
from typing import Any

from app.agents.keyword_resolver import RenderContext
from app.models.canonical_test import CanonicalTestCase, Locator
from app.models.enums import LocatorSource, LocatorStrategy, TargetFramework
from app.models.locator_resolution import (
    LocatorResolution,
    LocatorStatus,
    PlaywrightLocator,
    SeleniumLocator,
    UnresolvedLocatorReason,
)

_AI_SOURCES = frozenset({LocatorSource.AI_INFERRED, LocatorSource.AI_REPAIRED})
_QUOTED_VALUE = '"{value}"'


class MissingLocatorError(LookupError):
    """Raised when a locator is required for code generation but its resolution is UNRESOLVED."""

    def __init__(self, resolution: LocatorResolution) -> None:
        super().__init__(resolution.message or f"{resolution.object_name}: {resolution.status}")
        self.resolution = resolution


class UnsupportedLocatorStrategyError(LookupError):
    """The locator rules have no conversion for this strategy and target."""


class LocatorResolver:
    def __init__(self, locator_rules: dict[str, Any]) -> None:
        self._rules: dict[str, dict[str, dict[str, str]]] = locator_rules["strategies"]

    def resolve(
        self,
        object_name: str,
        object_repository: Mapping[str, Locator],
        step_sequences: Iterable[int] = (),
    ) -> LocatorResolution:
        sequences = list(step_sequences)
        locator = object_repository.get(object_name)
        if locator is None:
            return LocatorResolution(
                object_name=object_name,
                status=LocatorStatus.UNRESOLVED,
                step_sequences=sequences,
                reason=UnresolvedLocatorReason.MISSING_FROM_REPOSITORY,
                message=f"Object {object_name!r} is not defined in the object repository",
                suggestions=difflib.get_close_matches(object_name, list(object_repository), n=3, cutoff=0.75),
            )

        missing_targets = [target for target in TargetFramework if self._rule(locator.strategy, target) is None]
        if missing_targets:
            return LocatorResolution(
                object_name=object_name,
                status=LocatorStatus.UNRESOLVED,
                step_sequences=sequences,
                reason=UnresolvedLocatorReason.UNSUPPORTED_STRATEGY,
                message=(
                    f"Object {object_name!r} uses locator strategy '{locator.strategy}', which has no "
                    f"conversion rule for: {', '.join(missing_targets)}"
                ),
            )

        warnings = []
        if locator.source in _AI_SOURCES:
            warnings.append(
                f"Locator {object_name!r} is {locator.source} (confidence {locator.confidence:.2f}); "
                "review it before relying on the generated test"
            )
        return LocatorResolution(
            object_name=object_name,
            status=LocatorStatus.RESOLVED,
            locator=locator,
            step_sequences=sequences,
            warnings=warnings,
        )

    def resolve_test_case(self, test_case: CanonicalTestCase) -> list[LocatorResolution]:
        """One resolution per referenced object, in first-use order."""
        usages: dict[str, list[int]] = {}
        for step in test_case.steps:
            if step.object_name:
                usages.setdefault(step.object_name, []).append(step.sequence)
        return [self.resolve(name, test_case.object_repository, seqs) for name, seqs in usages.items()]

    @staticmethod
    def require(resolution: LocatorResolution) -> Locator:
        if not resolution.is_resolved or resolution.locator is None:
            raise MissingLocatorError(resolution)
        return resolution.locator

    def to_playwright(self, locator: Locator) -> PlaywrightLocator:
        rule = self._require_rule(locator.strategy, TargetFramework.PLAYWRIGHT)
        return PlaywrightLocator(selector=_render(rule["selector"], locator.value))

    def to_selenium(self, locator: Locator) -> SeleniumLocator:
        rule = self._require_rule(locator.strategy, TargetFramework.SELENIUM)
        return SeleniumLocator(by=rule["by"], value=_render(rule["value"], locator.value))

    def to_target(self, locator: Locator, target: TargetFramework) -> PlaywrightLocator | SeleniumLocator:
        if target is TargetFramework.PLAYWRIGHT:
            return self.to_playwright(locator)
        return self.to_selenium(locator)

    def render_context(self, locator: Locator, target: TargetFramework) -> RenderContext:
        converted = self.to_target(locator, target)
        if isinstance(converted, PlaywrightLocator):
            return RenderContext(locator=converted.selector)
        return RenderContext(by=converted.by, locator_value=converted.value)

    def _rule(self, strategy: LocatorStrategy, target: TargetFramework) -> dict[str, str] | None:
        return self._rules.get(strategy, {}).get(target)

    def _require_rule(self, strategy: LocatorStrategy, target: TargetFramework) -> dict[str, str]:
        rule = self._rule(strategy, target)
        if rule is None:
            raise UnsupportedLocatorStrategyError(f"Locator strategy '{strategy}' has no {target} conversion rule")
        return rule


def _render(template: str, value: str) -> str:
    if _QUOTED_VALUE in template:
        return template.replace(_QUOTED_VALUE, f'"{_css_string(value)}"')
    return template.replace("{value}", value)


def _css_string(value: str) -> str:
    """Escape a value for use inside a double-quoted CSS attribute selector string."""
    escaped = []
    for ch in value:
        if ch in '"\\':
            escaped.append("\\" + ch)
        elif ord(ch) < 0x20 or ch == "\x7f":
            escaped.append(f"\\{ord(ch):x} ")
        else:
            escaped.append(ch)
    return "".join(escaped)
