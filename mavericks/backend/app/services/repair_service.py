"""Self-healing loop: failure -> AI analysis -> minimal repair -> validation -> re-run -> compare -> history."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from app.agents.dom_locators import parse_dom, rank_candidates
from app.agents.repair_agent import RepairAgent, RepairNotPossibleError, with_locator, with_step_value
from app.execution.engine import ExecutionEngine
from app.execution.runner import ProjectNotAllowedError
from app.generators.base_generator import BaseGenerator
from app.models.capabilities import LOCATOR_STRATEGIES
from app.models.canonical_test import CanonicalTestCase
from app.models.enums import ArtifactKind, CheckStatus, RepairChangeType, RepairStatus, TargetFramework
from app.models.migration_record import MigrationRecord
from app.models.migration_result import GeneratedArtifact
from app.models.repair import RepairContext, RepairResult
from app.services.errors import MigrationStateError
from app.storage.migration_store import MigrationStore
from app.validation.engine import ValidationEngine

MAX_DOM_BYTES = 2_000_000

__all__ = ["RepairNotPossibleError", "RepairService", "current_test_case"]


class RepairService:
    def __init__(
        self,
        store: MigrationStore,
        agent: RepairAgent,
        generators: Mapping[TargetFramework, BaseGenerator],
        validation_engine: ValidationEngine,
        execution_engine: ExecutionEngine,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._agent = agent
        self._generators = generators
        self._validation = validation_engine
        self._execution = execution_engine
        self._clock = clock

    def repair(self, migration_id: str, target: TargetFramework, apply: bool) -> RepairResult:
        """Propose a repair for the last failed run; with apply=True also validate, re-run and record it."""
        with self._store.operation(migration_id):
            return self._repair(migration_id, target, apply)

    def _repair(self, migration_id: str, target: TargetFramework, apply: bool) -> RepairResult:
        record = self._store.get(migration_id)
        generated = record.results.get(target)
        if generated is None:
            raise MigrationStateError(f"Migration {migration_id} has no generated {target} project; generate it first")
        runs = record.executions.get(target, [])
        if not runs:
            raise MigrationStateError(f"The {target} project for {migration_id} has not been run yet")
        failure = runs[-1]
        if failure.passed:
            raise MigrationStateError(f"The last {target} run of {migration_id} passed; there is nothing to repair")
        if failure.failed_step is None:
            raise RepairNotPossibleError("The failure could not be traced to a test step")

        test_case = current_test_case(record, target)
        step = next((s for s in test_case.steps if s.sequence == failure.failed_step), None)
        if step is None:
            raise RepairNotPossibleError(f"Step {failure.failed_step} is not in the test case")
        locator = test_case.object_repository.get(step.object_name) if step.object_name else None
        try:
            project = self._execution.resolve_project(target, Path(generated.output_dir))
        except ProjectNotAllowedError:
            raise MigrationStateError(f"The generated project is outside the {target} workspace") from None

        dom = _read_dom(project, failure.dom_snapshots)
        candidates = []
        if dom and locator is not None and step.object_name:
            candidates = rank_candidates(
                parse_dom(dom),
                locator,
                step.object_name,
                test_case.object_repository,
                LOCATOR_STRATEGIES[test_case.application_type],
            )
        context = RepairContext(
            step=step,
            locator=locator,
            generated_code=_content(generated.artifacts, ArtifactKind.TEST),
            page_object_code=_content(generated.artifacts, ArtifactKind.PAGE_OBJECT),
            execution=failure,
            object_repository=test_case.object_repository,
            available_locators=candidates,
            dom_snapshot=dom,
        )
        proposal = self._agent.propose(context, test_case)
        repair = RepairResult(
            migration_id=migration_id,
            target_framework=target,
            proposal=proposal,
            original_step=step,
            original_locator=locator,
            original_execution=failure,
            created_at=self._clock(),
        )
        if not apply:
            return self._save(record, repair)

        if proposal.change_type is RepairChangeType.LOCATOR and proposal.new_locator is not None:
            repaired_case = with_locator(test_case, proposal.new_locator)
        else:
            repaired_case = with_step_value(test_case, proposal.step_sequence, proposal.new_value)
        generator = self._generators[target]
        artifacts = generator.build(repaired_case)
        validation = self._validation.validate_artifacts(artifacts, repaired_case, target)
        if not validation.passed:
            rejected = repair.model_copy(
                update={
                    "status": RepairStatus.REJECTED,
                    "validation_status": CheckStatus.FAIL,
                    "validation_summary": validation.summary,
                    "message": "The repaired code failed validation, so it was not applied",
                }
            )
            return self._save(record, rejected)

        generator.write(artifacts, project)
        try:
            rerun = self._execution.execute(target, project, attempt=len(runs) + 1)
        except Exception:
            generator.write(generated.artifacts, project)
            raise
        updates: dict[str, object] = {
            "executions": {**record.executions, target: [*runs, rerun]},
        }
        if rerun.passed:
            status, message, rolled_back = RepairStatus.PASSED_AFTER_REPAIR, "The repaired test passed on re-run", False
            updates["results"] = {
                **record.results,
                target: generated.model_copy(update={"artifacts": artifacts, "generated_at": self._clock()}),
            }
            updates["validations"] = {**record.validations, target: validation}
        else:
            generator.write(generated.artifacts, project)
            status, rolled_back = RepairStatus.FAILED, True
            message = "The repaired test still failed, so the original code was restored"
        repair = repair.model_copy(
            update={
                "status": status,
                "validation_status": validation.status,
                "validation_summary": validation.summary,
                "re_execution": rerun,
                "rolled_back": rolled_back,
                "message": message,
            }
        )
        return self._save(record.model_copy(update=updates), repair)

    def history(self, migration_id: str) -> list[RepairResult]:
        return list(self._store.get(migration_id).repairs)

    def _save(self, record: MigrationRecord, repair: RepairResult) -> RepairResult:
        self._store.save(record.model_copy(update={"repairs": [*record.repairs, repair]}))
        return repair


def current_test_case(record: MigrationRecord, target: TargetFramework) -> CanonicalTestCase:
    """The source test with this target's demo fault and verified repairs replayed in order; the source never changes."""
    test_case = record.test_case
    fault = record.demo_faults.get(target)
    for index, repair in enumerate(record.repairs):
        if fault is not None and index == fault.repairs_before:
            test_case = with_locator(test_case, fault.injected_locator)
        if repair.target_framework is not target or repair.status is not RepairStatus.PASSED_AFTER_REPAIR:
            continue
        proposal = repair.proposal
        if proposal.change_type is RepairChangeType.LOCATOR and proposal.new_locator is not None:
            test_case = with_locator(test_case, proposal.new_locator)
        elif proposal.change_type is RepairChangeType.WAIT:
            test_case = with_step_value(test_case, proposal.step_sequence, proposal.new_value)
    if fault is not None and fault.repairs_before >= len(record.repairs):
        test_case = with_locator(test_case, fault.injected_locator)
    return test_case


def _content(artifacts: list[GeneratedArtifact], kind: ArtifactKind) -> str:
    return next((a.content for a in artifacts if a.kind is kind), "")


def _read_dom(project: Path, snapshots: list[str]) -> str | None:
    for snapshot in snapshots:
        path = (project / snapshot).resolve()
        if path.is_relative_to(project) and path.is_file() and path.stat().st_size <= MAX_DOM_BYTES:
            return path.read_text(encoding="utf-8", errors="replace")
    return None
