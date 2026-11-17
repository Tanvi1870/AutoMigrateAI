"""Simulated qTest for the hackathon. Everything it returns is flagged `mock`; no network call is made."""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from app.models.base import CamelModel
from app.models.canonical_test import CanonicalTestCase
from app.models.traceability import AutomationLink, ExecutionUpdate, QTestCaseRef, QTestPublication, QTestRunRef
from app.qtest.base_adapter import BaseQTestAdapter, QTestError

FIRST_CASE_NUMBER = 1001


class MockRun(CamelModel):
    run_id: str
    update: ExecutionUpdate
    recorded_at: datetime
    published: bool = False


class MockTestCase(CamelModel):
    qtest_case_id: str
    source_test_case_id: str
    name: str
    description: str | None = None
    steps: list[str] = Field(default_factory=list)
    automation_links: list[AutomationLink] = Field(default_factory=list)
    runs: list[MockRun] = Field(default_factory=list)
    published_at: datetime | None = None


class MockRepository(CamelModel):
    next_case_number: int = FIRST_CASE_NUMBER
    next_run_number: int = 1
    test_cases: dict[str, MockTestCase] = Field(default_factory=dict)


class MockQTestAdapter(BaseQTestAdapter):
    """Issues 'QT-1001'-style IDs; persisted as JSON when a path is given, otherwise kept in memory."""

    is_mock = True
    display_name = "Mock qTest"

    def __init__(self, storage_path: Path | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._path = storage_path
        self._clock = clock
        self._lock = threading.Lock()
        self._repository = self._load()

    @property
    def repository(self) -> MockRepository:
        with self._lock:
            return self._repository.model_copy(deep=True)

    def create_test_case(self, test_case: CanonicalTestCase) -> QTestCaseRef:
        with self._lock:
            existing = next(
                (c for c in self._repository.test_cases.values() if c.source_test_case_id == test_case.test_case_id),
                None,
            )
            if existing is not None:
                return _ref(existing, created=False)
            case = MockTestCase(
                qtest_case_id=f"QT-{self._repository.next_case_number}",
                source_test_case_id=test_case.test_case_id,
                name=test_case.name,
                description=test_case.description,
                steps=[_describe_step(s.keyword, s.object_name, s.expected) for s in test_case.steps],
            )
            self._repository.next_case_number += 1
            self._repository.test_cases[case.qtest_case_id] = case
            self._save()
            return _ref(case, created=True)

    def link_automation(self, qtest_case_id: str, link: AutomationLink) -> None:
        with self._lock:
            case = self._case(qtest_case_id)
            case.automation_links = [
                existing for existing in case.automation_links if existing.target_framework != link.target_framework
            ] + [link]
            self._save()

    def update_execution(self, qtest_case_id: str, update: ExecutionUpdate) -> QTestRunRef:
        with self._lock:
            case = self._case(qtest_case_id)
            # The same run is identified by migration, target, attempt and start time: re-syncing it must not
            # duplicate it, and a different run (e.g. one from an earlier backend session) is never matched.
            key = (update.migration_id, update.target_framework, update.attempt, update.started_at)
            same = next(
                (
                    run
                    for run in case.runs
                    if (run.update.migration_id, run.update.target_framework, run.update.attempt, run.update.started_at)
                    == key
                ),
                None,
            )
            if same is None:
                same = MockRun(run_id=f"QR-{self._repository.next_run_number}", update=update, recorded_at=self._clock())
                self._repository.next_run_number += 1
                case.runs.append(same)
                self._save()
            return QTestRunRef(qtest_run_id=same.run_id, qtest_case_id=qtest_case_id, status=same.update.status, mock=True)

    def publish_result(self, qtest_case_id: str) -> QTestPublication:
        with self._lock:
            case = self._case(qtest_case_id)
            pending = [run for run in case.runs if not run.published]
            for run in pending:
                run.published = True
            case.published_at = self._clock()
            self._save()
            return QTestPublication(
                qtest_case_id=qtest_case_id, published_runs=len(pending), published_at=case.published_at, mock=True
            )

    def _case(self, qtest_case_id: str) -> MockTestCase:
        case = self._repository.test_cases.get(qtest_case_id)
        if case is None:
            raise QTestError(f"qTest test case {qtest_case_id} does not exist in Mock qTest")
        return case

    def _load(self) -> MockRepository:
        if self._path is None or not self._path.is_file():
            return MockRepository()
        return MockRepository.model_validate_json(self._path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._repository.model_dump(mode="json", by_alias=True), indent=2), encoding="utf-8"
        )
        temporary.replace(self._path)


def _ref(case: MockTestCase, created: bool) -> QTestCaseRef:
    return QTestCaseRef(
        qtest_case_id=case.qtest_case_id,
        source_test_case_id=case.source_test_case_id,
        name=case.name,
        created=created,
        mock=True,
    )


def _describe_step(keyword: str, object_name: str | None, expected: str | None) -> str:
    parts = [keyword, object_name or ""]
    if expected:
        parts.append(f"expects {expected!r}")
    return " ".join(part for part in parts if part)
