from collections.abc import Callable
from datetime import UTC, datetime

from app.agents.locator_resolver import LocatorResolver
from app.models.enums import RepairStatus, TargetFramework
from app.models.migration_record import MigrationRecord
from app.models.report import MigrationMetrics, MigrationReport
from app.services.qtest_service import step_traceability
from app.services.repair_service import current_test_case
from app.storage.migration_store import MigrationStore


class ReportService:
    def __init__(
        self,
        store: MigrationStore,
        locator_resolver: LocatorResolver,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._locator_resolver = locator_resolver
        self._clock = clock

    def build_report(self, migration_id: str, target: TargetFramework | None = None) -> MigrationReport:
        """Everything recorded for one target; without a target, the first one that has progressed."""
        record = self._store.get(migration_id)
        target = target or _default_target(record)
        plan = record.plans.get(target)
        resolutions = self._locator_resolver.resolve_test_case(current_test_case(record, target))
        validation = record.validations.get(target)
        executions = record.executions.get(target, [])
        repairs = [r for r in record.repairs if r.target_framework is target]
        result = record.results.get(target)
        artifacts = result.artifacts if result else []
        resolved = sum(1 for r in resolutions if r.is_resolved)
        metrics = MigrationMetrics(
            total_steps=len(record.test_case.steps),
            keywords_detected=len({step.keyword for step in record.test_case.steps}),
            keywords_mapped=plan.keywords_mapped if plan else None,
            locators_resolved=resolved,
            locators_unresolved=len(resolutions) - resolved,
            generated_files=sum(1 for a in artifacts if a.content),
            generated_lines=sum(len(a.content.splitlines()) for a in artifacts),
            validation_status=validation.status if validation else None,
            execution_status=executions[-1].status if executions else None,
            execution_count=len(executions),
            repair_count=len(repairs),
            passed_after_repair_count=sum(1 for r in repairs if r.status is RepairStatus.PASSED_AFTER_REPAIR),
            ai_migration_seconds=round(record.processing_seconds, 3) or None,
        )
        return MigrationReport(
            migration_id=migration_id,
            test_case_id=record.test_case.test_case_id,
            target_framework=target,
            generated_at=self._clock(),
            plan=plan,
            locator_resolutions=resolutions,
            validation=validation,
            executions=executions,
            repairs=repairs,
            traceability=step_traceability(record, target),
            qtest_links=[link for link in record.qtest_links if link.target_framework is target],
            metrics=metrics,
            demo_fault=record.demo_faults.get(target),
        )


def _default_target(record: MigrationRecord) -> TargetFramework:
    for stage in (record.executions, record.results, record.plans):
        if stage:
            return sorted(stage)[0]
    return TargetFramework.PLAYWRIGHT
