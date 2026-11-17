"""API contract tests: every endpoint, the full migration flow, and one consistent error shape."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.agents.migration_agent import MigrationAgent
from app.agents.migration_planner import MigrationPlanner
from app.agents.repair_agent import RepairAgent
from app.agents.test_analyzer import TestAnalyzer
from app.agents.test_data_injector import TestDataInjector
from app.config import PROJECT_ROOT, Settings, get_settings
from app.dependencies import (
    get_execution_service,
    get_migration_service,
    get_parser,
    get_qtest_service,
    get_repair_service,
    get_report_service,
    get_validation_service,
)
from app.execution.engine import ExecutionEngine
from app.execution.playwright_runner import PlaywrightRunner
from app.execution.selenium_runner import SeleniumRunner
from app.llm.mock_provider import MockLLMProvider
from app.main import create_app
from app.models import TargetFramework
from app.qtest.mock_qtest_adapter import MockQTestAdapter
from app.services.execution_service import ExecutionService
from app.services.migration_service import MigrationService, _demo_fault
from app.services.errors import MigrationStateError
from tests.factories import make_test_case
from app.services.qtest_service import QTestService
from app.services.repair_service import RepairService
from app.services.report_service import ReportService
from app.services.validation_service import ValidationService
from app.storage.migration_store import InMemoryMigrationStore, MigrationStore
from tests.test_repair import GENERATORS, V1, FakeBrowser
from tests.test_validation import ENGINE as VALIDATION, KEYWORDS, LOCATORS

SAMPLE = PROJECT_ROOT / "samples" / "icap" / "login_test.json"
ERROR_KEYS = {"status", "code", "message", "details"}


def build_app(generated: Path, browser: FakeBrowser, store: MigrationStore | None = None) -> FastAPI:
    store = store if store is not None else InMemoryMigrationStore()
    agent = MigrationAgent(
        TestAnalyzer(MockLLMProvider()), MigrationPlanner(KEYWORDS, LOCATORS, TestDataInjector()), GENERATORS
    )
    engine = ExecutionEngine(
        {
            TargetFramework.PLAYWRIGHT: PlaywrightRunner(generated, 60, True, process_runner=browser),
            TargetFramework.SELENIUM: SeleniumRunner(generated, 60, True, process_runner=browser),
        }
    )
    app = create_app()
    services = {
        get_migration_service: MigrationService(store, get_parser(), agent, generated, KEYWORDS),
        get_validation_service: ValidationService(store, VALIDATION),
        get_execution_service: ExecutionService(store, engine),
        get_repair_service: RepairService(store, RepairAgent(MockLLMProvider(), GENERATORS), GENERATORS, VALIDATION, engine),
        get_report_service: ReportService(store, LOCATORS),
        get_qtest_service: QTestService(store, MockQTestAdapter()),
    }
    for dependency, service in services.items():
        app.dependency_overrides[dependency] = _provider(service)
    return app


def _provider(service: object) -> Any:
    # A parameterless callable: FastAPI would treat any parameter as a request input.
    return lambda: service


@pytest.fixture
def generated(tmp_path: Path) -> Path:
    return tmp_path / "generated"


@pytest.fixture
def client(generated: Path) -> Iterator[TestClient]:
    with TestClient(build_app(generated, FakeBrowser())) as test_client:
        yield test_client


def upload(client: TestClient, content: bytes | None = None, filename: str = "login_test.json") -> dict[str, Any]:
    response = client.post(
        "/api/migration/upload", files={"file": (filename, SAMPLE.read_bytes() if content is None else content)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    body = response.json()
    assert response.status_code == status, body
    assert set(body) == ERROR_KEYS and body["status"] == status and body["code"] == code and body["message"]
    return body


def ready_for_execution(client: TestClient, target: str = "playwright") -> str:
    migration_id = upload(client)["migrationId"]
    for path in ("analyze", "generate", "validate"):
        response = client.post(f"/api/migration/{path}", json={"migrationId": migration_id, "targetFramework": target})
        assert response.status_code == 200, response.text
    return migration_id


class TestHealth:
    def test_health(self, client: TestClient) -> None:
        body = client.get("/api/health").json()
        assert body["status"] == "ok" and {"appName", "version", "llmProvider", "qtestMode"} <= body.keys()


class TestUpload:
    def test_upload_sample(self, client: TestClient) -> None:
        body = upload(client)
        assert body == {
            "migrationId": "MIG-001",
            "testCaseId": "ICAP-TC-001",
            "name": "Login Test",
            "stepsDetected": 5,
            "sourceFormat": body["sourceFormat"],
        }
        assert upload(client)["migrationId"] == "MIG-002"  # server-generated IDs, never the filename

    @pytest.mark.parametrize("target", ["playwright", "selenium"])
    def test_csv_upload_and_generation(self, client: TestClient, target: str) -> None:
        body = upload(client, SAMPLE.with_suffix(".csv").read_bytes(), "login_test.csv")
        assert body["stepsDetected"] == 5
        assert body["sourceFormat"] == "representative-icap-csv/1.0"
        for path in ("analyze", "generate", "validate"):
            response = client.post(
                f"/api/migration/{path}",
                json={"migrationId": body["migrationId"], "targetFramework": target},
            )
            assert response.status_code == 200, response.text
        assert response.json()["status"] == "PASS", response.text

    def test_invalid_csv_returns_parse_error(self, client: TestClient) -> None:
        response = client.post(
            "/api/migration/upload", files={"file": ("bad.csv", b"wrong,headers\n1,2")}
        )
        body = assert_error(response, 422, "INVALID_TEST_FILE")
        assert "Missing column" in str(body["details"])

    def test_rejects_unsupported_files(self, client: TestClient) -> None:
        response = client.post("/api/migration/upload", files={"file": ("test.exe", b"MZ")})
        assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")

    def test_invalid_test_file_lists_problems(self, client: TestClient) -> None:
        body = assert_error(
            client.post("/api/migration/upload", files={"file": ("bad.json", b"{not json")}), 422, "INVALID_TEST_FILE"
        )
        assert "not valid JSON" in body["message"]

    def test_invalid_canonical_content(self, client: TestClient) -> None:
        data = json.loads(SAMPLE.read_bytes())
        data["steps"] = []
        body = assert_error(
            client.post("/api/migration/upload", files={"file": ("x.json", json.dumps(data).encode())}),
            422,
            "INVALID_TEST_FILE",
        )
        assert body["details"]

    def test_missing_file(self, client: TestClient) -> None:
        body = assert_error(client.post("/api/migration/upload"), 422, "VALIDATION_ERROR")
        assert body["details"][0]["field"] == "file"

    def test_oversized_upload(self) -> None:
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: Settings(max_upload_bytes=10)
        with TestClient(app) as client:
            response = client.post("/api/migration/upload", files={"file": ("big.json", b"x" * 11)})
        assert_error(response, 413, "PAYLOAD_TOO_LARGE")


class TestAnalyzeAndGenerate:
    def test_analyze_returns_the_plan(self, client: TestClient) -> None:
        migration_id = upload(client)["migrationId"]
        plan = client.post("/api/migration/analyze", json={"migrationId": migration_id}).json()
        assert plan["status"] == "READY" and plan["targetFramework"] == "playwright"
        assert (plan["stepsDetected"], plan["keywordsMapped"], plan["locatorsResolved"]) == (5, 4, 4)
        assert plan["aiAnalysis"]["summary"]

    def test_generate_requires_a_plan(self, client: TestClient) -> None:
        migration_id = upload(client)["migrationId"]
        response = client.post("/api/migration/generate", json={"migrationId": migration_id, "targetFramework": "selenium"})
        assert "analyze it first" in assert_error(response, 409, "INVALID_STATE")["message"]

    @pytest.mark.parametrize("target", ["playwright", "selenium"])
    def test_generate_hides_server_paths(self, client: TestClient, generated: Path, target: str) -> None:
        migration_id = upload(client)["migrationId"]
        client.post("/api/migration/analyze", json={"migrationId": migration_id, "targetFramework": target})
        response = client.post("/api/migration/generate", json={"migrationId": migration_id, "targetFramework": target})
        body = response.json()
        assert response.status_code == 200 and body["projectPath"] == f"{target}/{migration_id}"
        assert "outputDir" not in body and str(generated.parent) not in response.text
        assert "tests/login_test.py" in {a["path"] for a in body["artifacts"]}
        assert (generated / target / migration_id / "tests" / "login_test.py").is_file()

    def test_unknown_keywords_are_rejected_at_upload(self, client: TestClient) -> None:
        data = json.loads(SAMPLE.read_bytes())
        data["steps"].append({"sequence": 4, "keyword": "web_hover", "object": "loginButton"})
        response = client.post("/api/migration/upload", files={"file": ("x.json", json.dumps(data).encode())})
        body = assert_error(response, 422, "INVALID_TEST_FILE")
        assert "unsupported keyword 'web_hover'" in body["details"][0]["message"]

    def test_blocked_plan_cannot_generate(self, client: TestClient) -> None:
        data = json.loads(SAMPLE.read_bytes())
        del data["objectRepository"]["loginButton"]
        migration_id = upload(client, json.dumps(data).encode())["migrationId"]
        plan = client.post("/api/migration/analyze", json={"migrationId": migration_id}).json()
        assert plan["status"] == "BLOCKED"
        body = assert_error(
            client.post("/api/migration/generate", json={"migrationId": migration_id}), 409, "MIGRATION_BLOCKED"
        )
        assert body["details"] == [{"field": None, "message": "Missing locator: loginButton"}]

    @pytest.mark.parametrize("target", ["playwright", "selenium"])
    def test_keyword_mappings(self, client: TestClient, target: str) -> None:
        migration_id = upload(client)["migrationId"]
        response = client.get(f"/api/migration/{migration_id}/keywords", params={"targetFramework": target})
        mappings = response.json()
        assert response.status_code == 200 and [m["stepSequence"] for m in mappings] == [1, 2, 3, 4, 5]
        assert {m["status"] for m in mappings} == {"RESOLVED"} and all(m["targetFramework"] == target for m in mappings)
        assert mappings[0]["keyword"] == "web_navigate" and mappings[0]["template"]


class TestFullFlow:
    def test_upload_to_qtest(self, client: TestClient) -> None:
        migration_id = ready_for_execution(client)

        execution = client.post("/api/migration/execute", json={"migrationId": migration_id}).json()
        assert (execution["status"], execution["failedStep"], execution["exitCode"]) == ("FAIL", 4, 1)

        repair = client.post("/api/migration/repair", json={"migrationId": migration_id, "apply": True}).json()
        assert repair["status"] == "PASSED_AFTER_REPAIR" and repair["outcome"] == "FAIL at step 4 -> PASS"
        assert [stage["title"] for stage in repair["timeline"]][-1] == "PASS"

        links = client.post("/api/qtest/sync", json={"migrationId": migration_id}).json()
        assert links[0]["qTestCaseId"] == "QT-1001" and links[0]["executionStatus"] == "PASS" and links[0]["mock"]

        report = client.get(f"/api/migration/{migration_id}/report").json()
        assert report["targetFramework"] == "playwright" and report["plan"]["status"] == "READY"
        assert report["validation"]["status"] in ("PASS", "WARNING")
        assert [e["status"] for e in report["executions"]] == ["FAIL", "PASS"]
        assert [r["status"] for r in report["repairs"]] == ["PASSED_AFTER_REPAIR"]
        assert len(report["traceability"]) == 5 and report["traceability"][3]["qTestCaseId"] == "QT-1001"
        assert report["qtestLinks"][0]["generatedScript"] == f"playwright/{migration_id}/tests/login_test.py"
        assert report["metrics"]["aiMigrationSeconds"] > 0 and report["unresolvedLocators"] == []
        metrics = report["metrics"]
        assert (metrics["totalSteps"], metrics["keywordsDetected"], metrics["keywordsMapped"]) == (5, 4, 4)
        assert (metrics["locatorsResolved"], metrics["locatorsUnresolved"]) == (4, 0)
        assert metrics["generatedFiles"] > 0 and metrics["generatedLines"] > metrics["generatedFiles"]
        assert metrics["validationStatus"] == report["validation"]["status"] and metrics["executionStatus"] == "PASS"
        assert (metrics["executionCount"], metrics["repairCount"], metrics["passedAfterRepairCount"]) == (2, 1, 1)
        history = client.get(f"/api/migration/{migration_id}/repairs").json()
        assert [r["status"] for r in history] == ["PASSED_AFTER_REPAIR"]

    def test_execute_requires_validation(self, client: TestClient) -> None:
        migration_id = upload(client)["migrationId"]
        client.post("/api/migration/analyze", json={"migrationId": migration_id})
        client.post("/api/migration/generate", json={"migrationId": migration_id})
        response = client.post("/api/migration/execute", json={"migrationId": migration_id})
        assert "validate it first" in assert_error(response, 409, "INVALID_STATE")["message"]

    def test_regenerating_discards_the_old_validation(self, client: TestClient) -> None:
        migration_id = ready_for_execution(client)
        client.post("/api/migration/generate", json={"migrationId": migration_id})
        assert_error(client.post("/api/migration/execute", json={"migrationId": migration_id}), 409, "INVALID_STATE")

    def test_repair_after_passing_run_is_a_conflict(self, generated: Path) -> None:
        with TestClient(build_app(generated, FakeBrowser(html=V1))) as client:
            migration_id = ready_for_execution(client)
            assert client.post("/api/migration/execute", json={"migrationId": migration_id}).json()["status"] == "PASS"
            response = client.post("/api/migration/repair", json={"migrationId": migration_id})
        assert "nothing to repair" in assert_error(response, 409, "INVALID_STATE")["message"]

    def test_unrepairable_failure(self, generated: Path) -> None:
        with TestClient(build_app(generated, FakeBrowser(html='<p id="x">?</p>'))) as client:
            migration_id = ready_for_execution(client)
            client.post("/api/migration/execute", json={"migrationId": migration_id})
            response = client.post("/api/migration/repair", json={"migrationId": migration_id})
        assert_error(response, 422, "REPAIR_NOT_POSSIBLE")

    def test_report_for_a_fresh_upload(self, client: TestClient) -> None:
        migration_id = upload(client)["migrationId"]
        report = client.get(f"/api/migration/{migration_id}/report", params={"targetFramework": "selenium"}).json()
        assert report["targetFramework"] == "selenium" and report["plan"] is None and report["executions"] == []
        assert len(report["locatorResolutions"]) == 4 and report["metrics"]["aiMigrationSeconds"] is None
        metrics = report["metrics"]
        assert (metrics["totalSteps"], metrics["keywordsMapped"], metrics["generatedFiles"]) == (5, None, 0)
        assert metrics["validationStatus"] is None and metrics["executionStatus"] is None and metrics["repairCount"] == 0


class DemoBrowser:
    """The real (v1) demo page: only a DEMO MODE locator fails, everything else passes."""

    def __init__(self) -> None:
        self.failing, self.passing = FakeBrowser(html=V1, always_fail=True), FakeBrowser(html=V1)

    def __call__(self, command: Any, cwd: Path, env: Any, timeout: float) -> Any:
        broken = "am-demo-missing" in (cwd / "pages" / "login_page.py").read_text(encoding="utf-8")
        return (self.failing if broken else self.passing)(command, cwd, env, timeout)


class TestDemoMode:
    @pytest.fixture
    def demo_client(self, generated: Path) -> Iterator[TestClient]:
        with TestClient(build_app(generated, DemoBrowser())) as test_client:
            yield test_client

    @staticmethod
    def generate(client: TestClient, migration_id: str, demo: bool) -> dict[str, Any]:
        response = client.post(
            "/api/migration/generate", json={"migrationId": migration_id, "simulateLocatorFailure": demo}
        )
        assert response.status_code == 200, response.text
        return response.json()

    def run_demo(self, client: TestClient, migration_id: str) -> dict[str, Any]:
        project = self.generate(client, migration_id, demo=True)
        assert client.post("/api/migration/validate", json={"migrationId": migration_id}).json()["status"] == "PASS"
        failure = client.post("/api/migration/execute", json={"migrationId": migration_id}).json()
        assert (failure["status"], failure["failedStep"]) == ("FAIL", project["demoMode"]["stepSequence"])
        return client.post("/api/migration/repair", json={"migrationId": migration_id, "apply": True}).json()

    def test_failure_is_repaired_to_pass(self, demo_client: TestClient, generated: Path) -> None:
        migration_id = upload(demo_client)["migrationId"]
        demo_client.post("/api/migration/analyze", json={"migrationId": migration_id})
        project = self.generate(demo_client, migration_id, demo=True)
        demo = project["demoMode"]
        assert demo["label"] == "DEMO MODE" and (demo["stepSequence"], demo["objectName"]) == (4, "loginButton")
        assert demo["injectedLocator"] == {
            "name": "loginButton", "strategy": "id", "value": "am-demo-missing-loginButton",
            "source": "OBJECT_REPOSITORY", "confidence": None,
        }
        assert demo["repairsBefore"] == 0  # persisted with the record so the fault replays after a restart
        page = (generated / "playwright" / migration_id / "pages" / "login_page.py").read_text(encoding="utf-8")
        assert "am-demo-missing-loginButton" in page and "loginBtn" not in page

        repair = self.run_demo(demo_client, migration_id)
        assert repair["status"] == "PASSED_AFTER_REPAIR" and repair["outcome"] == "FAIL at step 4 -> PASS"
        assert (repair["proposal"]["oldValue"], repair["proposal"]["newValue"]) == (
            "id=am-demo-missing-loginButton",
            "id=loginBtn",
        )
        report = demo_client.get(f"/api/migration/{migration_id}/report").json()
        assert report["demoFault"]["label"] == "DEMO MODE"
        assert [e["status"] for e in report["executions"]] == ["FAIL", "PASS"]

    def test_normal_generation_never_injects_a_fault(self, demo_client: TestClient, generated: Path) -> None:
        migration_id = upload(demo_client)["migrationId"]
        demo_client.post("/api/migration/analyze", json={"migrationId": migration_id})
        self.generate(demo_client, migration_id, demo=True)
        project = self.generate(demo_client, migration_id, demo=False)
        assert project["demoMode"] is None
        page = (generated / "playwright" / migration_id / "pages" / "login_page.py").read_text(encoding="utf-8")
        assert "am-demo-missing" not in page
        demo_client.post("/api/migration/validate", json={"migrationId": migration_id})
        assert demo_client.post("/api/migration/execute", json={"migrationId": migration_id}).json()["status"] == "PASS"
        assert demo_client.get(f"/api/migration/{migration_id}/report").json()["demoFault"] is None

    def test_demo_can_be_repeated_after_a_repair(self, demo_client: TestClient) -> None:
        migration_id = upload(demo_client)["migrationId"]
        demo_client.post("/api/migration/analyze", json={"migrationId": migration_id})
        assert self.run_demo(demo_client, migration_id)["status"] == "PASSED_AFTER_REPAIR"
        assert self.run_demo(demo_client, migration_id)["status"] == "PASSED_AFTER_REPAIR"

    def test_demo_step_is_deterministic(self) -> None:
        test_case = make_test_case()
        assert _demo_fault(test_case, 0).step_sequence == _demo_fault(test_case, 0).step_sequence == 4
        no_click = test_case.model_copy(update={"steps": [s for s in test_case.steps if s.keyword != "web_click"]})
        fault = _demo_fault(no_click, 0)
        first_with_object = next(s for s in no_click.steps if s.object_name in no_click.object_repository)
        assert (fault.step_sequence, fault.object_name) == (first_with_object.sequence, first_with_object.object_name)

    def test_demo_needs_a_locator(self) -> None:
        test_case = make_test_case()
        no_objects = test_case.model_copy(update={"steps": [s for s in test_case.steps if s.object_name is None]})
        with pytest.raises(MigrationStateError, match="DEMO MODE needs a step that uses a locator"):
            _demo_fault(no_objects, 0)


class TestConsistentErrors:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("post", "/api/migration/analyze", {"migrationId": "MIG-404"}),
            ("post", "/api/migration/generate", {"migrationId": "MIG-404"}),
            ("post", "/api/migration/validate", {"migrationId": "MIG-404"}),
            ("post", "/api/migration/execute", {"migrationId": "MIG-404"}),
            ("post", "/api/migration/repair", {"migrationId": "MIG-404"}),
            ("post", "/api/qtest/sync", {"migrationId": "MIG-404"}),
            ("get", "/api/migration/MIG-404/report", None),
            ("get", "/api/migration/MIG-404/repairs", None),
            ("get", "/api/migration/MIG-404/keywords", None),
            ("get", "/api/qtest/MIG-404/traceability", None),
        ],
    )
    def test_unknown_migration_everywhere(self, client: TestClient, method: str, path: str, payload: Any) -> None:
        response = getattr(client, method)(path, **({"json": payload} if payload else {}))
        assert assert_error(response, 404, "MIGRATION_NOT_FOUND")["message"] == "Migration MIG-404 not found"

    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"migrationId": "../etc"}, "migrationId"),
            ({"migrationId": "MIG-001", "targetFramework": "cypress"}, "targetFramework"),
            ({}, "migrationId"),
        ],
    )
    def test_request_validation(self, client: TestClient, payload: dict, field: str) -> None:
        body = assert_error(client.post("/api/migration/analyze", json=payload), 422, "VALIDATION_ERROR")
        assert body["details"][0]["field"] == field
        assert "../etc" not in json.dumps(body) and "cypress" not in json.dumps(body)  # inputs are not echoed

    def test_invalid_path_id(self, client: TestClient) -> None:
        assert_error(client.get("/api/migration/abc/report"), 422, "VALIDATION_ERROR")

    def test_unknown_route_and_method(self, client: TestClient) -> None:
        assert_error(client.get("/api/nope"), 404, "NOT_FOUND")
        assert_error(client.get("/api/migration/upload"), 405, "METHOD_NOT_ALLOWED")

    def test_unexpected_errors_hide_internals(self) -> None:
        app = create_app()
        router = APIRouter()

        @router.get("/api/boom")
        def boom() -> None:
            raise RuntimeError(r"secret C:\internal\path")

        app.include_router(router)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/boom")
        body = assert_error(response, 500, "INTERNAL_ERROR")
        assert body["message"] == "An unexpected error occurred" and "secret" not in response.text


class TestOpenApi:
    def test_documents_every_endpoint_and_the_error_model(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        expected = {
            ("get", "/api/health"),
            ("post", "/api/migration/upload"),
            ("post", "/api/migration/analyze"),
            ("post", "/api/migration/generate"),
            ("post", "/api/migration/validate"),
            ("post", "/api/migration/execute"),
            ("post", "/api/migration/repair"),
            ("get", "/api/migration/{migration_id}/report"),
            ("post", "/api/qtest/sync"),
        }
        documented = {(method, path) for path, ops in schema["paths"].items() for method in ops}
        assert expected <= documented
        assert "ErrorResponse" in schema["components"]["schemas"]
        assert "outputDir" not in json.dumps(schema["components"]["schemas"]["GeneratedProject"])
        analyze = schema["paths"]["/api/migration/analyze"]["post"]["responses"]
        assert analyze["404"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
