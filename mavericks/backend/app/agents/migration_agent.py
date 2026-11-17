"""Migration engine: Canonical Test -> AI analysis -> Migration Plan -> KB/locator-driven generation -> validation.

The LLM is reached only through the TestAnalyzer's LLMProvider and only returns advisory JSON; test code
comes exclusively from the target generators, which use the keyword knowledge base and locator resolver.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from app.agents.migration_planner import MigrationPlanner
from app.agents.test_analyzer import TestAnalyzer
from app.generators.base_generator import BaseGenerator, GenerationError
from app.models.canonical_test import CanonicalTestCase
from app.models.enums import PlanStatus, TargetFramework
from app.models.migration_plan import MigrationPlan
from app.models.migration_result import GeneratedArtifact, MigrationResult
from app.validation.validators import SyntaxValidator


class MigrationBlockedError(ValueError):
    """Generation was requested for a plan that is blocked or does not belong to the test case."""

    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        self.reasons = reasons or []
        super().__init__(message if not self.reasons else f"{message}: {'; '.join(self.reasons)}")


class MigrationAgent:
    def __init__(
        self,
        analyzer: TestAnalyzer,
        planner: MigrationPlanner,
        generators: Mapping[TargetFramework, BaseGenerator],
    ) -> None:
        self._analyzer = analyzer
        self._planner = planner
        self._generators = generators

    def plan(self, migration_id: str, test_case: CanonicalTestCase, target: TargetFramework) -> MigrationPlan:
        analysis = self._analyzer.analyze(test_case)
        plan = self._planner.build(migration_id, test_case, target, analysis)
        if target not in self._generators:
            plan = plan.model_copy(update={"errors": [*plan.errors, f"No generator is registered for {target}"]})
        return plan

    def generate(
        self, migration_id: str, test_case: CanonicalTestCase, plan: MigrationPlan, output_dir: Path
    ) -> MigrationResult:
        if plan.migration_id != migration_id or plan.test_case_id != test_case.test_case_id:
            raise MigrationBlockedError(f"Plan {plan.migration_id}/{plan.test_case_id} does not match this migration")
        if plan.status is PlanStatus.BLOCKED:
            raise MigrationBlockedError(f"Migration {migration_id} is blocked", plan.blocking_reasons)
        generator = self._generators.get(plan.target_framework)
        if generator is None:
            raise MigrationBlockedError(f"No generator is registered for {plan.target_framework}")

        artifacts = generator.build(test_case)
        _check_syntax(test_case.test_case_id, artifacts)
        generator.write(artifacts, output_dir)
        generator.write_secrets(test_case, output_dir)
        return MigrationResult(
            migration_id=migration_id,
            test_case_id=test_case.test_case_id,
            target_framework=plan.target_framework,
            output_dir=output_dir.as_posix(),
            artifacts=artifacts,
            generated_at=datetime.now(UTC),
        )


def _check_syntax(test_case_id: str, artifacts: list[GeneratedArtifact]) -> None:
    """Validation gate before anything is written; the full ValidationEngine runs on the written project."""
    issues = SyntaxValidator.check_sources({artifact.path: artifact.content for artifact in artifacts})
    if issues:
        raise GenerationError(test_case_id, [issue.reason for issue in issues])
