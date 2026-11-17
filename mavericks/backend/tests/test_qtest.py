import json
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.dependencies import get_qtest_service
from app.main import create_app
from app.models import (
    AutomationLink,
    CanonicalTestCase,
    ExecutionStatus,
    ExecutionUpdate,
    MigrationRecord,
    MigrationResult,
    TargetFramework,
)
from app.qtest.base_adapter import BaseQTestAdapter, QTestError
from app.qtest.factory import create_qtest_adapter
from app.qtest.mock_qtest_adapter import MockQTestAdapter
from app.qtest.qtest_adapter import QTestAdapter, QTestUnavailableError
from app.services.errors import MigrationStateError
from app.services.qtest_service import QTestService
from app.storage.migration_store import InMemoryMigrationStore
from tests.factories import NOW, make_execution, make_test_case
from tests.test_repair import GENERATORS

PW, SE = TargetFramework.PLAYWRIGHT, TargetFramework.SELENIUM


def update(attempt: int = 1, status: ExecutionStatus = ExecutionStatus.PASS, target: TargetFramework = PW):
    return ExecutionUpdate(
        migration_id="MIG-001",
        target_framework=target,
        attempt=attempt,
        status=status,
        started_at=NOW,
        duration_seconds=1.5,
    )


def link(target: TargetFramework = PW, script: str = "playwright/MIG-001/tests/login_test.py") -> AutomationLink:
    return AutomationLink(migration_id="MIG-001", target_framework=target, script_path=script)


class TestMockQTestAdapter:
    def test_creates_sequential_ids_and_reuses_existing_cases(self) -> None:
        adapter = MockQTestAdapter()
        first = adapter.create_test_case(make_test_case())
        again = adapter.create_test_case(make_test_case())
        other = adapter.create_test_case(make_test_case(testCaseId="ICAP-TC-002", name="Checkout"))
        assert (first.qtest_case_id, first.created, first.mock) == ("QT-1001", True, True)
        assert (again.qtest_case_id, again.created) == ("QT-1001", False)
        assert other.qtest_case_id == "QT-1002"
        case = adapter.repository.test_cases["QT-1001"]
        assert case.source_test_case_id == "ICAP-TC-001" and case.steps[-1] == "web_assertText welcomeMessage expects 'Welcome'"

    def test_links_runs_and_publishing(self) -> None:
        adapter = MockQTestAdapter(clock=lambda: NOW)
        case_id = adapter.create_test_case(make_test_case()).qtest_case_id
        adapter.link_automation(case_id, link())
        adapter.link_automation(case_id, link(script="playwright/MIG-001/tests/v2_test.py"))
        adapter.link_automation(case_id, link(SE, "selenium/MIG-001/tests/login_test.py"))
        first = adapter.update_execution(case_id, update(1, ExecutionStatus.FAIL))
        duplicate = adapter.update_execution(case_id, update(1, ExecutionStatus.FAIL))
        second = adapter.update_execution(case_id, update(2))
        assert (first.qtest_run_id, duplicate.qtest_run_id, second.qtest_run_id) == ("QR-1", "QR-1", "QR-2")
        assert first.mock and second.status is ExecutionStatus.PASS

        published = adapter.publish_result(case_id)
        assert (published.published_runs, published.published_at, published.mock) == (2, NOW, True)
        assert adapter.publish_result(case_id).published_runs == 0

        case = adapter.repository.test_cases[case_id]
        assert [l.script_path for l in case.automation_links] == [
            "playwright/MIG-001/tests/v2_test.py",
            "selenium/MIG-001/tests/login_test.py",
        ]
        assert all(run.published for run in case.runs) and len(case.runs) == 2

    def test_unknown_case_is_an_error(self) -> None:
        adapter = MockQTestAdapter()
        for call in (
            lambda: adapter.link_automation("QT-9999", link()),
            lambda: adapter.update_execution("QT-9999", update()),
            lambda: adapter.publish_result("QT-9999"),
        ):
            with pytest.raises(QTestError, match="QT-9999 does not exist in Mock qTest"):
                call()

    def test_persists_as_json(self, tmp_path: Path) -> None:
        path = tmp_path / "data" / "mock_qtest.json"
        adapter = MockQTestAdapter(path)
        case_id = adapter.create_test_case(make_test_case()).qtest_case_id
        adapter.update_execution(case_id, update())
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["testCases"]["QT-1001"]["sourceTestCaseId"] == "ICAP-TC-001"

        reopened = MockQTestAdapter(path)
        assert reopened.create_test_case(make_test_case()).created is False
        assert reopened.create_test_case(make_test_case(testCaseId="ICAP-TC-002")).qtest_case_id == "QT-1002"
        assert reopened.update_execution(case_id, update(2)).qtest_run_id == "QR-2"
        assert not path.with_suffix(".tmp").exists()

    def test_never_touches_the_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", _no_network)
        adapter = MockQTestAdapter()
        case_id = adapter.create_test_case(make_test_case()).qtest_case_id
        adapter.link_automation(case_id, link())
        adapter.update_execution(case_id, update())
        adapter.publish_result(case_id)


def _no_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("no network call is allowed")


class TestRealQTestAdapter:
    def _adapter(self) -> QTestAdapter:
        return QTestAdapter("https://example.qtestnet.com/", SecretStr("token"), "12345")

    @pytest.mark.parametrize(
        "call",
        [
            lambda a: a.create_test_case(make_test_case()),
            lambda a: a.link_automation("1", link()),
            lambda a: a.update_execution("1", update()),
            lambda a: a.publish_result("1"),
        ],
        ids=["create_test_case", "link_automation", "update_execution", "publish_result"],
    )
    def test_never_fakes_a_call(self, call: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", _no_network)
        with pytest.raises(QTestUnavailableError, match="not been confirmed") as excinfo:
            call(self._adapter())
        assert isinstance(excinfo.value, NotImplementedError)
        assert "token" not in str(excinfo.value)

    @pytest.mark.parametrize(
        ("url", "project", "message"),
        [("http://example.com", "1", "https"), ("https://example.com", "abc", "numeric")],
    )
    def test_validates_configuration(self, url: str, project: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            QTestAdapter(url, SecretStr("t"), project)

    def test_labels(self) -> None:
        assert (QTestAdapter.is_mock, QTestAdapter.display_name) == (False, "qTest")
        assert (MockQTestAdapter.is_mock, MockQTestAdapter.display_name) == (True, "Mock qTest")


class TestFactory:
    def test_mock_is_the_default(self, tmp_path: Path) -> None:
        adapter = create_qtest_adapter(Settings(qtest_mode="mock", data_dir=tmp_path))
        assert isinstance(adapter, MockQTestAdapter)
        adapter.create_test_case(make_test_case())
        assert (tmp_path / "mock_qtest.json").is_file()

    def test_real_mode_requires_explicit_credentials(self) -> None:
        settings = Settings(qtest_mode="real", qtest_base_url=None, qtest_api_token=None, qtest_project_id=None)
        with pytest.raises(ValueError, match="QTEST_BASE_URL, QTEST_API_TOKEN, QTEST_PROJECT_ID"):
            create_qtest_adapter(settings)

    def test_real_mode_with_credentials(self) -> None:
        settings = Settings(
            qtest_mode="real", qtest_base_url="https://x.qtestnet.com", qtest_api_token="t", qtest_project_id="7"
        )
        assert isinstance(create_qtest_adapter(settings), QTestAdapter)


def seeded(executions: dict[TargetFramework, list] | None = None, targets: tuple = (PW, SE)) -> InMemoryMigrationStore:
    test_case = make_test_case()
    results = {
        target: MigrationResult(
            migration_id="MIG-001",
            test_case_id=test_case.test_case_id,
            target_framework=target,
            output_dir=f"/srv/generated/{target}/MIG-001",
            artifacts=GENERATORS[target].build(test_case),
            generated_at=NOW,
        )
        for target in targets
    }
    store = InMemoryMigrationStore()
    store.save(
        MigrationRecord(
            migration_id="MIG-001",
            created_at=NOW,
            test_case=test_case,
            results=results,
            executions=executions or {},
        )
    )
    return store


def runs() -> dict[TargetFramework, list]:
    return {
        PW: [
            make_execution(
                ExecutionStatus.FAIL,
                PW,
                artifacts_dir="artifacts/runs/r1-attempt1",
                screenshots=["artifacts/runs/r1-attempt1/screenshots/login.png"],
            ),
            make_execution(ExecutionStatus.FAIL, PW, attempt=2, artifacts_dir="artifacts/runs/r2-attempt2",
                           started_at=NOW + timedelta(minutes=1)),
        ],
        SE: [make_execution(target=SE, artifacts_dir="artifacts/runs/r3-attempt1")],
    }


class TestQTestService:
    def test_sync_builds_end_to_end_traceability(self) -> None:
        store, adapter = seeded(runs()), MockQTestAdapter()
        links = QTestService(store, adapter).sync("MIG-001")
        pw, se = links
        assert {link.qtest_case_id for link in links} == {"QT-1001"}
        assert (pw.source_test_case_id, pw.generated_script) == ("ICAP-TC-001", "playwright/MIG-001/tests/login_test.py")
        assert (pw.execution_status, pw.attempt, pw.qtest_run_id) == (ExecutionStatus.FAIL, 2, "QR-1")
        assert pw.logs == [
            "playwright/MIG-001/artifacts/runs/r2-attempt2/test-run.log",
            "playwright/MIG-001/artifacts/runs/r2-attempt2/junit.xml",
        ]
        assert (se.generated_script, se.execution_status, se.qtest_run_id) == (
            "selenium/MIG-001/tests/login_test.py",
            ExecutionStatus.PASS,
            "QR-2",
        )
        assert all(link.published and link.mock for link in links)
        assert store.get("MIG-001").qtest_links == links
        assert "/srv/" not in json.dumps([link.model_dump(mode="json") for link in links])

    def test_evidence_includes_screenshots(self) -> None:
        executions = runs()
        executions[PW] = executions[PW][:1]
        pw = QTestService(seeded(executions), MockQTestAdapter()).sync("MIG-001")[0]
        assert pw.screenshots == ["playwright/MIG-001/artifacts/runs/r1-attempt1/screenshots/login.png"]

    def test_resync_is_idempotent(self) -> None:
        store, adapter = seeded(runs()), MockQTestAdapter()
        service = QTestService(store, adapter)
        first, second = service.sync("MIG-001"), service.sync("MIG-001")
        assert first == second
        case = adapter.repository.test_cases["QT-1001"]
        assert len(case.runs) == 2 and len(case.automation_links) == 2

    def test_scripts_without_runs_are_linked_but_not_published(self) -> None:
        adapter = MockQTestAdapter()
        links = QTestService(seeded(), adapter).sync("MIG-001")
        assert [(l.execution_status, l.qtest_run_id, l.published) for l in links] == [(None, None, False)] * 2
        assert adapter.repository.test_cases["QT-1001"].published_at is None

    def test_requires_generated_scripts(self) -> None:
        with pytest.raises(MigrationStateError, match="generate them first"):
            QTestService(seeded(targets=()), MockQTestAdapter()).sync("MIG-001")

    def test_real_adapter_records_nothing(self) -> None:
        store = seeded(runs())
        adapter = QTestAdapter("https://x.qtestnet.com", SecretStr("t"), "7")
        with pytest.raises(QTestUnavailableError):
            QTestService(store, adapter).sync("MIG-001")
        assert store.get("MIG-001").qtest_links == []

    def test_step_level_traceability(self) -> None:
        store = seeded(runs())
        service = QTestService(store, MockQTestAdapter())
        before = service.traceability("MIG-001")
        assert before.qtest_system == "Mock qTest" and before.links == []
        assert all(step.qtest_case_id is None for step in before.steps)

        service.sync("MIG-001")
        report = service.traceability("MIG-001")
        pw_steps = [s for s in report.steps if s.target_framework is PW]
        se_steps = [s for s in report.steps if s.target_framework is SE]
        assert [s.execution_status for s in pw_steps] == [
            ExecutionStatus.PASS, ExecutionStatus.PASS, ExecutionStatus.PASS, ExecutionStatus.FAIL, None
        ]
        assert all(s.execution_status is ExecutionStatus.PASS for s in se_steps)
        test_artifact = next(a for a in store.get("MIG-001").results[PW].artifacts if a.path == "tests/login_test.py")
        assert [s.generated_line for s in pw_steps] == [test_artifact.step_line_map[n] for n in range(1, 6)]
        assert pw_steps[1].source_step_ref == "BLK-LOGIN#1" and pw_steps[1].block_id == "BLK-LOGIN"
        assert {s.qtest_case_id for s in report.steps} == {"QT-1001"}
        assert pw_steps[0].generated_file == "playwright/MIG-001/tests/login_test.py"


class _BrokenQTest(MockQTestAdapter):
    def link_automation(self, qtest_case_id: str, link: AutomationLink) -> None:
        raise QTestError("qTest rejected the link")


class TestQTestEndpoints:
    def _client(self, service: QTestService) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_qtest_service] = lambda: service
        return TestClient(app)

    def test_sync_and_traceability(self) -> None:
        with self._client(QTestService(seeded(runs()), MockQTestAdapter())) as client:
            synced = client.post("/api/qtest/sync", json={"migrationId": "MIG-001"})
            report = client.get("/api/qtest/MIG-001/traceability")
        assert synced.status_code == 200
        first = synced.json()[0]
        assert {"sourceTestCaseId", "generatedScript", "qTestCaseId", "executionStatus", "logs", "screenshots",
                "qTestRunId", "mock"} <= first.keys()
        assert report.status_code == 200 and report.json()["qtestSystem"] == "Mock qTest"
        assert report.json()["steps"][0]["qTestCaseId"] == "QT-1001"

    @pytest.mark.parametrize(
        ("adapter", "store", "status"),
        [
            (MockQTestAdapter(), InMemoryMigrationStore(), 404),
            (MockQTestAdapter(), seeded(targets=()), 409),
            (_BrokenQTest(), seeded(), 502),
            (QTestAdapter("https://x.qtestnet.com", SecretStr("t"), "7"), seeded(), 501),
        ],
        ids=["unknown", "not-generated", "qtest-error", "real-unavailable"],
    )
    def test_errors(self, adapter: BaseQTestAdapter, store: InMemoryMigrationStore, status: int) -> None:
        with self._client(QTestService(store, adapter)) as client:
            assert client.post("/api/qtest/sync", json={"migrationId": "MIG-001"}).status_code == status


def test_source_test_is_never_changed() -> None:
    store = seeded(runs())
    before: CanonicalTestCase = store.get("MIG-001").test_case
    QTestService(store, MockQTestAdapter()).sync("MIG-001")
    assert store.get("MIG-001").test_case == before
