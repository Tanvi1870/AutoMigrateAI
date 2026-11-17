"""Builds the MigrationPlan from the test analysis plus the keyword KB, locator resolver and test data checks."""

from collections.abc import Collection

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver
from app.agents.navigation_guard import NavigationGuard
from app.agents.test_data_injector import TestDataInjector
from app.models.canonical_test import CanonicalTestCase
from app.models.enums import TargetFramework
from app.models.keyword_resolution import KeywordResolution, ResolutionStatus, UnsupportedReason
from app.models.migration_plan import MigrationPlan, TestAnalysis

_UNSUPPORTED = frozenset({UnsupportedReason.UNKNOWN_KEYWORD, UnsupportedReason.TARGET_NOT_MAPPED})


class MigrationPlanner:
    """Deterministic: every count and blocking finding comes from the knowledge bases, never from the LLM."""

    def __init__(
        self,
        keyword_resolver: KeywordResolver,
        locator_resolver: LocatorResolver,
        data_injector: TestDataInjector,
        navigation: NavigationGuard | None = None,
    ) -> None:
        self._keyword_resolver = keyword_resolver
        self._locator_resolver = locator_resolver
        self._data_injector = data_injector
        self._navigation = navigation

    def build(
        self, migration_id: str, test_case: CanonicalTestCase, target: TargetFramework, analysis: TestAnalysis
    ) -> MigrationPlan:
        resolutions = self._keyword_resolver.resolve_test_case(test_case, target)
        locators = self._locator_resolver.resolve_test_case(test_case)
        keywords = analysis.keywords_detected

        warnings = list(analysis.warnings)
        warnings += _keyword_warnings(resolutions)
        warnings += [warning for r in locators for warning in r.warnings]
        if not analysis.has_assertion:
            warnings.append("The test has no assertion step")

        ai = analysis.ai_analysis
        return MigrationPlan(
            migration_id=migration_id,
            test_case_id=test_case.test_case_id,
            application_type=test_case.application_type,
            target_framework=target,
            steps_detected=analysis.steps_detected,
            keywords_detected=len(keywords),
            keywords_mapped=sum(self._keyword_resolver.resolve(k, target).is_resolved for k in keywords),
            locators_resolved=sum(r.is_resolved for r in locators),
            data_parameters=len(self._data_injector.find_parameters(test_case)),
            reusable_blocks=analysis.reusable_blocks,
            unsupported_keywords=_keywords_with(resolutions, _UNSUPPORTED),
            future_adapter_keywords=_keywords_with(resolutions, {UnsupportedReason.FUTURE_ADAPTER}),
            missing_locators=[r.object_name for r in locators if not r.is_resolved],
            unresolved_variables=self._data_injector.find_unresolved(test_case),
            warnings=list(dict.fromkeys(warnings)),
            errors=_step_errors(resolutions) + (self._navigation.problems(test_case) if self._navigation else []),
            summary=ai.summary if ai else None,
            ai_analysis=ai,
        )


def _keywords_with(resolutions: list[KeywordResolution], reasons: Collection[UnsupportedReason]) -> list[str]:
    return list(dict.fromkeys(r.keyword for r in resolutions if r.reason in reasons))


def _step_errors(resolutions: list[KeywordResolution]) -> list[str]:
    errors = []
    for r in resolutions:
        if r.status is ResolutionStatus.INVALID:
            errors += [f"Step {r.step_sequence}: {issue.message}" for issue in r.issues]
        elif r.reason is UnsupportedReason.APPLICATION_TYPE_MISMATCH:
            errors.append(f"Step {r.step_sequence}: {r.message}")
    return errors


def _keyword_warnings(resolutions: list[KeywordResolution]) -> list[str]:
    warnings = []
    for r in resolutions:
        if r.is_resolved and r.assumption:
            warnings.append(f"{r.keyword}: {r.assumption}")
        warnings += [f"Step {r.step_sequence}: {warning}" for warning in r.warnings]
    return warnings
