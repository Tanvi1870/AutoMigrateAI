"""qTest sync and end-to-end traceability:
ICAP test case <-> generated script <-> qTest test case <-> execution status <-> logs/screenshots."""

from pathlib import PurePosixPath

from app.models.enums import ArtifactKind, ExecutionStatus, TargetFramework
from app.models.execution_result import ExecutionResult
from app.models.migration_record import MigrationRecord
from app.models.migration_result import MigrationResult
from app.models.traceability import AutomationLink, ExecutionUpdate, QTestLink, TraceabilityRecord, TraceabilityReport
from app.qtest.base_adapter import BaseQTestAdapter
from app.services.errors import MigrationStateError
from app.storage.migration_store import MigrationStore


class QTestService:
    def __init__(self, store: MigrationStore, adapter: BaseQTestAdapter) -> None:
        self._store = store
        self._adapter = adapter

    def sync(self, migration_id: str) -> list[QTestLink]:
        """Create/reuse the qTest case, link every generated script, record the latest runs, publish."""
        with self._store.operation(migration_id):
            return self._sync(migration_id)

    def _sync(self, migration_id: str) -> list[QTestLink]:
        record = self._store.get(migration_id)
        if not record.results:
            raise MigrationStateError(f"Migration {migration_id} has no generated scripts; generate them first")
        case = self._adapter.create_test_case(record.test_case)
        links = []
        for target in sorted(record.results):
            script = _workspace_path(target, migration_id, _test_file(record.results[target]))
            self._adapter.link_automation(
                case.qtest_case_id, AutomationLink(migration_id=migration_id, target_framework=target, script_path=script)
            )
            runs = record.executions.get(target, [])
            last = runs[-1] if runs else None
            run_ref = None
            if last is not None:
                run_ref = self._adapter.update_execution(case.qtest_case_id, _update(migration_id, target, last))
            links.append(
                QTestLink(
                    source_test_case_id=record.test_case.test_case_id,
                    generated_script=script,
                    qtest_case_id=case.qtest_case_id,
                    execution_status=last.status if last else None,
                    target_framework=target,
                    qtest_run_id=run_ref.qtest_run_id if run_ref else None,
                    attempt=last.attempt if last else None,
                    executed_at=last.started_at if last else None,
                    logs=_logs(migration_id, target, last),
                    screenshots=[_workspace_path(target, migration_id, s) for s in last.screenshots] if last else [],
                    mock=self._adapter.is_mock,
                )
            )
        if any(link.qtest_run_id for link in links):
            self._adapter.publish_result(case.qtest_case_id)
            links = [link.model_copy(update={"published": link.qtest_run_id is not None}) for link in links]
        self._store.save(record.model_copy(update={"qtest_links": links}))
        return links

    def traceability(self, migration_id: str) -> TraceabilityReport:
        record = self._store.get(migration_id)
        return TraceabilityReport(
            migration_id=migration_id,
            qtest_system=self._adapter.display_name,
            links=record.qtest_links,
            steps=[step for target in sorted(record.results) for step in step_traceability(record, target)],
        )


def step_traceability(record: MigrationRecord, target: TargetFramework) -> list[TraceabilityRecord]:
    """Source step <-> generated file/line <-> last run's step status <-> qTest case, for one target."""
    result = record.results.get(target)
    if result is None:
        return []
    qtest_id = next((link.qtest_case_id for link in record.qtest_links if link.target_framework == target), None)
    test_artifact = next(a for a in result.artifacts if a.kind is ArtifactKind.TEST)
    runs = record.executions.get(target, [])
    last = runs[-1] if runs else None
    return [
        TraceabilityRecord(
            source_test_case_id=record.test_case.test_case_id,
            source_step_ref=step.source_step_ref,
            step_sequence=step.sequence,
            keyword=step.keyword,
            block_id=step.block_id,
            target_framework=target,
            generated_file=_workspace_path(target, record.migration_id, test_artifact.path),
            generated_line=test_artifact.step_line_map.get(step.sequence),
            execution_status=_step_status(last, step.sequence),
            qtest_case_id=qtest_id,
        )
        for step in record.test_case.steps
    ]


def _test_file(result: MigrationResult) -> str:
    return next(a.path for a in result.artifacts if a.kind is ArtifactKind.TEST)


def _workspace_path(target: TargetFramework, migration_id: str, relative: str) -> str:
    """Paths are shared relative to the generated workspace, never as absolute server paths."""
    return str(PurePosixPath(target, migration_id, relative))


def _logs(migration_id: str, target: TargetFramework, execution: ExecutionResult | None) -> list[str]:
    if execution is None or execution.artifacts_dir is None:
        return []
    return [
        _workspace_path(target, migration_id, f"{execution.artifacts_dir}/{name}")
        for name in ("test-run.log", "junit.xml")
    ]


def _update(migration_id: str, target: TargetFramework, execution: ExecutionResult) -> ExecutionUpdate:
    return ExecutionUpdate(
        migration_id=migration_id,
        target_framework=target,
        attempt=execution.attempt,
        status=execution.status,
        started_at=execution.started_at,
        duration_seconds=execution.duration_seconds,
        failed_step=execution.failed_step,
        error_message=execution.error_message,
        logs=_logs(migration_id, target, execution),
        screenshots=[_workspace_path(target, migration_id, s) for s in execution.screenshots],
    )


def _step_status(execution: ExecutionResult | None, sequence: int) -> ExecutionStatus | None:
    """Per-step status from the run: steps before the failure passed, later steps did not run."""
    if execution is None:
        return None
    if execution.passed:
        return ExecutionStatus.PASS
    if execution.failed_step is None:
        return None
    if sequence < execution.failed_step:
        return ExecutionStatus.PASS
    return execution.status if sequence == execution.failed_step else None
