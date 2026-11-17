import re
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.agents.keyword_resolver import KeywordResolver
from app.agents.migration_agent import MigrationAgent
from app.models.canonical_test import CanonicalTestCase, Locator
from app.models.demo import DemoFault
from app.models.enums import LocatorStrategy, TargetFramework
from app.models.keyword_resolution import KeywordResolution
from app.models.migration_plan import MigrationPlan
from app.models.migration_record import MigrationRecord
from app.models.migration_result import GeneratedProject, UploadResponse
from app.parsers.base_parser import BaseParser
from app.services.errors import MigrationStateError
from app.services.repair_service import current_test_case
from app.storage.migration_store import MigrationStore

ALLOWED_SUFFIXES = (".json", ".csv")


class UnsupportedUploadError(ValueError):
    """The uploaded file type is not accepted."""


class MigrationService:
    def __init__(
        self,
        store: MigrationStore,
        parser: BaseParser,
        agent: MigrationAgent,
        generated_dir: Path,
        keywords: KeywordResolver,
    ) -> None:
        self._store = store
        self._parser = parser
        self._agent = agent
        self._generated_dir = generated_dir
        self._keywords = keywords

    def upload(self, filename: str, content: bytes) -> UploadResponse:
        """Parse the source test into the canonical model and store it under a server-generated ID."""
        if not filename.lower().endswith(ALLOWED_SUFFIXES):
            raise UnsupportedUploadError("Only .json and .csv test case exports are accepted")
        test_case = self._parser.parse(content, source_name=PurePosixPath(filename.replace("\\", "/")).name)
        record = MigrationRecord(migration_id=self._store.next_id(), created_at=datetime.now(UTC), test_case=test_case)
        self._store.save(record)
        return UploadResponse(
            migration_id=record.migration_id,
            test_case_id=test_case.test_case_id,
            name=test_case.name,
            steps_detected=len(test_case.steps),
            source_format=test_case.source.format if test_case.source else self._parser.format_name,
        )

    def analyze(self, migration_id: str, target: TargetFramework) -> MigrationPlan:
        with self._store.operation(migration_id):
            return self._analyze(migration_id, target)

    def _analyze(self, migration_id: str, target: TargetFramework) -> MigrationPlan:
        record = self._store.get(migration_id)
        start = time.monotonic()
        plan = self._agent.plan(migration_id, record.test_case, target)
        self._store.save(
            record.model_copy(
                update={
                    "plans": {**record.plans, target: plan},
                    "processing_seconds": record.processing_seconds + time.monotonic() - start,
                }
            )
        )
        return plan

    def keyword_mappings(self, migration_id: str, target: TargetFramework) -> list[KeywordResolution]:
        """How each source step maps to the target framework, straight from the keyword knowledge base."""
        record = self._store.get(migration_id)
        return self._keywords.resolve_test_case(current_test_case(record, target), target)

    def generate(
        self, migration_id: str, target: TargetFramework, simulate_locator_failure: bool = False
    ) -> GeneratedProject:
        """Generate into generated/<target>/<migration_id>/; any earlier validation of that project is discarded.

        simulate_locator_failure is DEMO MODE: one known locator is broken on purpose. Without it, any earlier
        demo fault for this target is cleared, so normal generation never carries an injected failure.
        """
        with self._store.operation(migration_id):
            return self._generate(migration_id, target, simulate_locator_failure)

    def _generate(self, migration_id: str, target: TargetFramework, simulate_locator_failure: bool) -> GeneratedProject:
        record = self._store.get(migration_id)
        plan = record.plans.get(target)
        if plan is None:
            raise MigrationStateError(f"Migration {migration_id} has no {target} plan; analyze it first")
        faults = {t: f for t, f in record.demo_faults.items() if t != target}
        record = record.model_copy(update={"demo_faults": faults})
        if simulate_locator_failure:
            faults[target] = _demo_fault(current_test_case(record, target), len(record.repairs))
            record = record.model_copy(update={"demo_faults": faults})
        start = time.monotonic()
        result = self._agent.generate(
            migration_id, current_test_case(record, target), plan, self._generated_dir / target / migration_id
        )
        validations = {t: v for t, v in record.validations.items() if t != target}
        self._store.save(
            record.model_copy(
                update={
                    "results": {**record.results, target: result},
                    "validations": validations,
                    "processing_seconds": record.processing_seconds + time.monotonic() - start,
                }
            )
        )
        return GeneratedProject(
            migration_id=migration_id,
            test_case_id=result.test_case_id,
            target_framework=target,
            project_path=f"{target}/{migration_id}",
            artifacts=result.artifacts,
            generated_at=result.generated_at,
            demo_mode=faults.get(target),
        )


def _demo_fault(test_case: CanonicalTestCase, repairs_before: int) -> DemoFault:
    """Pick the known demo step deterministically: the first click on a mapped object, else any mapped object."""
    steps = [s for s in test_case.steps if s.object_name in test_case.object_repository]
    step = next((s for s in steps if s.keyword == "web_click"), steps[0] if steps else None)
    if step is None or step.object_name is None:
        raise MigrationStateError("DEMO MODE needs a step that uses a locator, and this test case has none")
    original = test_case.object_repository[step.object_name]
    injected = Locator(
        name=step.object_name,
        strategy=LocatorStrategy.ID,
        value=f"am-demo-missing-{re.sub(r'[^A-Za-z0-9_-]+', '-', step.object_name)}",
    )
    return DemoFault(
        step_sequence=step.sequence,
        keyword=step.keyword,
        object_name=step.object_name,
        original_locator=original,
        injected_locator=injected,
        description=(
            f"DEMO MODE: step {step.sequence} ({step.keyword} {step.object_name}) was generated with the "
            f"deliberately wrong locator id={injected.value} instead of {original.strategy}={original.value}."
        ),
        repairs_before=repairs_before,
    )
