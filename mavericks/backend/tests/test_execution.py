import json
import os
import sys
import textwrap
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_execution_service
from app.execution.engine import ExecutionCapacityError, ExecutionEngine
from app.execution.playwright_runner import PlaywrightRunner
from app.execution.runner import MAX_OUTPUT_CHARS, ProcessOutput, ProjectNotAllowedError, run_process
from app.execution.selenium_runner import SeleniumRunner
from app.generators.playwright_generator import PlaywrightGenerator
from app.main import create_app
from app.models import (
    CheckStatus,
    ExecutionStatus,
    MigrationRecord,
    MigrationResult,
    TargetFramework,
    ValidationResult,
)
from app.services.errors import MigrationStateError
from app.services.execution_service import ExecutionService
from app.storage.migration_store import InMemoryMigrationStore
from tests.factories import NOW, make_test_case
from tests.test_validation import KEYWORDS, LOCATORS, TEMPLATES

PW, SE = TargetFramework.PLAYWRIGHT, TargetFramework.SELENIUM


def junit(*cases: str) -> str:
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{"".join(cases)}</testsuite></testsuites>'


PASSED_CASE = '<testcase classname="tests.login_test" name="test_login" time="1.2"/>'


class FakeProcess:
    """Stands in for the pytest subprocess: records the call and writes the run's report files."""

    def __init__(
        self,
        output: ProcessOutput = ProcessOutput(0, "1 passed", ""),
        report: str | None = junit(PASSED_CASE),
        log: str = "INFO starting test_login",
        screenshot: bool = False,
    ) -> None:
        self.output, self.report, self.log, self.screenshot = output, report, log, screenshot
        self.calls: list[tuple[list[str], Path, dict[str, str], float]] = []

    def __call__(self, command: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> ProcessOutput:
        self.calls.append((list(command), cwd, dict(env), timeout))
        run_dir = Path(env["AM_ARTIFACTS_DIR"])
        if self.report is not None:
            (run_dir / "junit.xml").write_text(self.report, encoding="utf-8")
        (run_dir / "test-run.log").write_text(self.log, encoding="utf-8")
        if self.screenshot:
            (run_dir / "screenshots").mkdir()
            (run_dir / "screenshots" / "tests_login_test.py_test_login.png").write_bytes(b"\x89PNG")
        return self.output


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "generated"


def generated_project(root: Path, target: TargetFramework = PW, migration_id: str = "MIG-001") -> Path:
    project = root / target / migration_id
    PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES).generate(make_test_case(), project)
    return project


def runner(root: Path, process: Callable[..., ProcessOutput], target: TargetFramework = PW, **kwargs: object):
    cls = PlaywrightRunner if target is PW else SeleniumRunner
    return cls(root, kwargs.pop("timeout", 60), kwargs.pop("headless", True), process_runner=process)


class TestOutcomes:
    def test_pass_captures_everything(self, root: Path) -> None:
        project = generated_project(root)
        process = FakeProcess(output=ProcessOutput(0, "1 passed in 1.2s", "a warning"))
        result = runner(root, process).run(project)
        assert result.status is ExecutionStatus.PASS and result.passed
        assert (result.exit_code, result.stdout, result.stderr) == (0, "1 passed in 1.2s", "a warning")
        assert result.logs == "INFO starting test_login"
        assert (result.tests_total, result.tests_failed) == (1, 0)
        assert result.duration_seconds >= 0 and result.target_framework is PW and result.attempt == 1
        assert result.artifacts_dir.startswith("artifacts/runs/") and result.screenshots == []

    def test_failure_maps_to_step_and_collects_screenshots(self, root: Path) -> None:
        project = generated_project(root)
        source = (project / "tests/login_test.py").read_text(encoding="utf-8").splitlines()
        click_line = next(n for n, line in enumerate(source, 1) if ".click()" in line)
        failure = (
            '<testcase classname="tests.login_test" name="test_login">'
            '<failure message="playwright._impl._errors.TimeoutError: Locator.click: Timeout 10000ms exceeded.">'
            f"page = ...\n&gt;   page.locator(LoginPage.LOGIN_BUTTON).click()\n\n"
            f"tests\\login_test.py:{click_line}: \n.venv\\site-packages\\playwright\\sync_api.py:99: TimeoutError"
            "</failure></testcase>"
        )
        process = FakeProcess(output=ProcessOutput(1, "1 failed", ""), report=junit(failure), screenshot=True)
        result = runner(root, process).run(project)
        assert result.status is ExecutionStatus.FAIL and result.exit_code == 1
        assert result.error_type == "TimeoutError"
        assert result.error_message.startswith("playwright._impl._errors.TimeoutError: Locator.click")
        assert result.failed_step == 4
        assert (result.tests_total, result.tests_failed) == (1, 1)
        assert result.screenshots == [f"{result.artifacts_dir}/screenshots/tests_login_test.py_test_login.png"]

    def test_plain_assertion_failure(self, root: Path) -> None:
        project = generated_project(root)
        failure = '<testcase name="test_login"><failure message="assert \'Hi\' == \'Welcome\'">x</failure></testcase>'
        result = runner(root, FakeProcess(ProcessOutput(1, "", ""), junit(failure))).run(project)
        assert (result.status, result.error_type, result.failed_step) == (ExecutionStatus.FAIL, "AssertionError", None)

    def test_setup_error_is_an_error(self, root: Path) -> None:
        project = generated_project(root)
        error = (
            '<testcase name="test_login"><error message="failed on setup with '
            '&quot;selenium.common.exceptions.SessionNotCreatedException: no chrome&quot;">trace</error></testcase>'
        )
        result = runner(root, FakeProcess(ProcessOutput(1, "", ""), junit(error)), SE).run(
            generated_project(root, SE)
        )
        assert result.status is ExecutionStatus.ERROR and result.target_framework is SE
        assert "SessionNotCreatedException" in result.error_message
        assert project.exists()

    @pytest.mark.parametrize(
        ("exit_code", "message"),
        [(2, "Test run was interrupted"), (4, "pytest usage error"), (5, "No tests were collected"), (9, "code 9")],
    )
    def test_pytest_exit_codes(self, root: Path, exit_code: int, message: str) -> None:
        process = FakeProcess(ProcessOutput(exit_code, "", "ERROR: something\n"), report=None)
        result = runner(root, process).run(generated_project(root))
        assert result.status is ExecutionStatus.ERROR and result.exit_code == exit_code
        assert message in result.error_message and result.error_message.endswith("ERROR: something")

    def test_failure_without_report(self, root: Path) -> None:
        result = runner(root, FakeProcess(ProcessOutput(1, "boom", ""), report=None)).run(generated_project(root))
        assert result.status is ExecutionStatus.FAIL and result.error_type == "TestFailure"

    def test_timeout(self, root: Path) -> None:
        process = FakeProcess(ProcessOutput(None, "partial output", "", timed_out=True), report=None)
        result = runner(root, process, timeout=7).run(generated_project(root))
        assert result.status is ExecutionStatus.TIMEOUT and result.exit_code is None
        assert result.error_message == "Test run exceeded 7s and was stopped"
        assert result.stdout == "partial output"
        assert process.calls[0][3] == 7

    def test_process_that_cannot_start(self, root: Path) -> None:
        def fail(*args: object) -> ProcessOutput:
            raise FileNotFoundError("python missing")

        result = runner(root, fail).run(generated_project(root))
        assert result.status is ExecutionStatus.ERROR
        assert result.error_message == "pytest did not run: Could not start pytest: FileNotFoundError: python missing"

    def test_long_output_keeps_the_tail(self, root: Path) -> None:
        output = "x" * MAX_OUTPUT_CHARS + "SUMMARY"
        result = runner(root, FakeProcess(ProcessOutput(0, output, ""))).run(generated_project(root))
        assert result.stdout.startswith("[... 7 characters truncated ...]") and result.stdout.endswith("SUMMARY")

    def test_each_run_gets_its_own_artifacts(self, root: Path) -> None:
        project = generated_project(root)
        first = runner(root, FakeProcess()).run(project, attempt=1)
        second = runner(root, FakeProcess()).run(project, attempt=2)
        assert first.artifacts_dir != second.artifacts_dir and second.artifacts_dir.endswith("-attempt2")


class TestCommandAndSecurity:
    def test_fixed_command_without_shell(self, root: Path) -> None:
        project = generated_project(root)
        process = FakeProcess()
        runner(root, process).run(project)
        command, cwd, env, _ = process.calls[0]
        run_dir = Path(env["AM_ARTIFACTS_DIR"])
        assert command == [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--junitxml={run_dir / 'junit.xml'}",
            "-o",
            f"log_file={run_dir / 'test-run.log'}",
        ]
        assert cwd == project.resolve() and run_dir.is_relative_to(project.resolve() / "artifacts" / "runs")

    @pytest.mark.parametrize(("target", "flag"), [(PW, "--headed"), (SE, "--selenium-headed")])
    def test_headed_flag(self, root: Path, target: TargetFramework, flag: str) -> None:
        process = FakeProcess()
        runner(root, process, target, headless=False).run(generated_project(root, target))
        assert process.calls[0][0][-1] == flag

    def test_secrets_are_not_passed_to_tests(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for name, value in {
            "AZURE_OPENAI_API_KEY": "k",
            "QTEST_API_TOKEN": "t",
            "GITHUB_TOKEN": "g",
            "PYTEST_ADDOPTS": "--pdb",
            "AM_DATA_PASSWORD": "override",
        }.items():
            monkeypatch.setenv(name, value)
        process = FakeProcess()
        runner(root, process).run(generated_project(root))
        env = process.calls[0][2]
        assert not {"AZURE_OPENAI_API_KEY", "QTEST_API_TOKEN", "GITHUB_TOKEN", "PYTEST_ADDOPTS"} & env.keys()
        assert env["AM_DATA_PASSWORD"] == "override" and "PATH" in {k.upper() for k in env}

    def test_container_browser_settings_reach_the_tests(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = {
            "AM_BROWSER_ARGS": "--no-sandbox --disable-dev-shm-usage",
            "AM_CHROME_BINARY": "/usr/bin/chromium",
            "SE_CHROMEDRIVER": "/usr/bin/chromedriver",
            "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
        }
        for name, value in settings.items():
            monkeypatch.setenv(name, value)
        process = FakeProcess()
        runner(root, process, SE).run(generated_project(root, SE))
        env = process.calls[0][2]
        assert {name: env.get(name) for name in settings} == settings

    def test_environment_is_an_allowlist(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        leaked = {
            "AWS_ACCESS_KEY_ID": "AKIA",
            "DATABASE_URL": "postgres://u:p@db/x",
            "SERVICE_CONNECTION_STRING": "Server=x;Password=y",
            "AZURE_CLIENT_CREDENTIALS": "c",
            "SE_REMOTE_TOKEN": "t",
            "PYTEST_PLUGINS": "evil",
            "AM_ARTIFACTS_DIR": "/elsewhere",
        }
        for name, value in leaked.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("AM_DATA_BASE_URL", "http://localhost:8001/demo")
        process = FakeProcess()
        runner(root, process).run(generated_project(root))
        env = {k.upper(): v for k, v in process.calls[0][2].items()}
        assert not (leaked.keys() - {"AM_ARTIFACTS_DIR"}) & env.keys()
        assert env["AM_ARTIFACTS_DIR"] != "/elsewhere" and env["AM_DATA_BASE_URL"] == "http://localhost:8001/demo"

    def test_navigation_allowlist_reaches_the_tests(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AM_ALLOWED_URLS", '["https://evil.example/"]')  # the server's own value wins
        process = FakeProcess()
        allowed = ["http://localhost:8001/demo/"]
        PlaywrightRunner(root, 60, True, process_runner=process, allowed_urls=allowed).run(generated_project(root))
        assert json.loads(process.calls[0][2]["AM_ALLOWED_URLS"]) == allowed

    def test_invalid_allowlist_entries_fail_at_startup(self, root: Path) -> None:
        with pytest.raises(ValueError, match="must use http or https"):
            PlaywrightRunner(root, 60, True, allowed_urls=["file:///etc/"])

    def test_rejects_projects_outside_the_workspace(self, root: Path, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere"
        PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES).generate(make_test_case(), outside)
        process = FakeProcess()
        for project in (outside, root / "playwright" / ".." / ".." / "elsewhere", root / "playwright"):
            with pytest.raises(ProjectNotAllowedError):
                runner(root, process).run(project)
        assert process.calls == []

    def test_rejects_other_targets_workspace(self, root: Path) -> None:
        with pytest.raises(ProjectNotAllowedError, match="outside the playwright workspace"):
            runner(root, FakeProcess()).run(generated_project(root, SE))

    def test_rejects_directories_that_are_not_generated_projects(self, root: Path) -> None:
        project = root / "playwright" / "MIG-001"
        project.mkdir(parents=True)
        with pytest.raises(ProjectNotAllowedError, match="not a generated pytest project"):
            runner(root, FakeProcess()).run(project)

    def test_rejects_symlink_escape(self, root: Path, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere"
        PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES).generate(make_test_case(), outside)
        link = root / "playwright" / "MIG-LINK"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("creating symlinks needs extra privileges on this machine")
        with pytest.raises(ProjectNotAllowedError):
            runner(root, FakeProcess()).run(link)

    def test_timeout_must_be_positive(self, root: Path) -> None:
        with pytest.raises(ValueError, match="positive"):
            PlaywrightRunner(root, 0, True)


class TestRealSubprocess:
    """Runs real pytest subprocesses on browser-free projects, so no browser or network is needed."""

    CONFTEST = textwrap.dedent(
        """
        import os
        from pathlib import Path

        import pytest


        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_makereport(item, call):
            report = yield
            if report.when == "call" and report.failed:
                folder = Path(os.environ["AM_ARTIFACTS_DIR"]) / "screenshots"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{item.name}.png").write_bytes(b"png")
            return report
        """
    )
    TESTS = textwrap.dedent(
        """
        import logging
        import time

        log = logging.getLogger(__name__)


        def test_ok():
            # [AM-STEP 1] web_navigate
            log.info("step one")


        def test_broken():
            # [AM-STEP 1] web_navigate
            log.info("navigating")
            # [AM-STEP 2] web_assertText welcomeMessage
            print("to stdout")
            assert "Hello" == "Welcome"


        def test_slow():
            time.sleep(int(__import__("os").environ.get("AM_DATA_SLEEP", "0")))
        """
    )

    def _project(self, root: Path) -> Path:
        project = root / "playwright" / "MIG-REAL"
        (project / "tests").mkdir(parents=True)
        (project / "pytest.ini").write_text(
            "[pytest]\ntestpaths = tests\npythonpath = .\nlog_file_level = INFO\n", encoding="utf-8"
        )
        (project / "conftest.py").write_text(self.CONFTEST, encoding="utf-8")
        (project / "tests" / "login_test.py").write_text(self.TESTS, encoding="utf-8")
        (project / "tests" / "__init__.py").write_text("", encoding="utf-8")
        return project

    def test_real_failing_run(self, root: Path) -> None:
        result = PlaywrightRunner(root, 120, True, process_runner=run_process).run(self._project(root))
        assert result.status is ExecutionStatus.FAIL and result.exit_code == 1
        assert (result.tests_total, result.tests_failed) == (3, 1)
        assert result.failed_step == 2 and result.error_type == "AssertionError"
        assert "to stdout" in result.stdout and "1 failed" in result.stdout
        assert "navigating" in result.logs and "step one" in result.logs
        assert result.screenshots == [f"{result.artifacts_dir}/screenshots/test_broken.png"]

    def test_real_timeout_kills_the_run(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AM_DATA_SLEEP", "60")
        result = PlaywrightRunner(root, 5, True, process_runner=run_process).run(self._project(root))
        assert result.status is ExecutionStatus.TIMEOUT and result.exit_code is None
        assert result.duration_seconds < 30


class TestExecutionEngine:
    def test_dispatches_to_the_target_runner(self, root: Path) -> None:
        process = FakeProcess()
        engine = ExecutionEngine({PW: runner(root, process), SE: runner(root, process, SE)})
        assert engine.execute(SE, generated_project(root, SE), attempt=3).attempt == 3
        assert engine.execute(PW, generated_project(root)).target_framework is PW

    def test_rejects_mismatched_registration(self, root: Path) -> None:
        with pytest.raises(ValueError, match="registered for selenium"):
            ExecutionEngine({SE: runner(root, FakeProcess())})

    def test_unknown_target(self, root: Path) -> None:
        with pytest.raises(ValueError, match="No runner"):
            ExecutionEngine({}).execute(PW, generated_project(root))

    def test_concurrent_runs_are_capped(self, root: Path) -> None:
        project = generated_project(root)
        started, release = threading.Event(), threading.Event()

        def slow(command: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> ProcessOutput:
            started.set()
            release.wait(10)
            return FakeProcess()(command, cwd, env, timeout)

        engine = ExecutionEngine({PW: runner(root, slow)}, max_concurrent=1)
        first = threading.Thread(target=engine.execute, args=(PW, project))
        first.start()
        assert started.wait(10)
        try:
            with pytest.raises(ExecutionCapacityError, match="All 1 test execution slot"):
                engine.execute(PW, project)
        finally:
            release.set()
            first.join(10)
        assert engine.execute(PW, project, attempt=2).status is ExecutionStatus.PASS  # the slot is free again

    def test_capacity_error_is_http_429(self, root: Path) -> None:
        class Full(ExecutionEngine):
            def execute(self, *args: object, **kwargs: object):  # noqa: ANN202
                raise ExecutionCapacityError("All 2 test execution slot(s) are busy")

        app = create_app()
        app.dependency_overrides[get_execution_service] = lambda: ExecutionService(seeded_store(root), Full({}))
        with TestClient(app) as client:
            response = client.post("/api/migration/execute", json={"migrationId": "MIG-001"})
        assert response.status_code == 429 and response.json()["code"] == "EXECUTION_CAPACITY_REACHED"


def seeded_store(root: Path, validation: CheckStatus | None = CheckStatus.PASS, generated: bool = True):
    store = InMemoryMigrationStore()
    test_case = make_test_case()
    results, validations = {}, {}
    if generated:
        project = generated_project(root)
        results[PW] = MigrationResult(
            migration_id="MIG-001",
            test_case_id=test_case.test_case_id,
            target_framework=PW,
            output_dir=project.as_posix(),
            artifacts=PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES).build(test_case),
            generated_at=NOW,
        )
    if validation is not None:
        validations[PW] = ValidationResult(syntax=validation)
    store.save(
        MigrationRecord(
            migration_id="MIG-001", created_at=NOW, test_case=test_case, results=results, validations=validations
        )
    )
    return store


def service(root: Path, store: InMemoryMigrationStore, process: FakeProcess | None = None) -> ExecutionService:
    return ExecutionService(store, ExecutionEngine({PW: runner(root, process or FakeProcess())}))


class TestExecutionService:
    def test_runs_and_records_attempts(self, root: Path) -> None:
        store = seeded_store(root)
        first = service(root, store).execute("MIG-001", PW)
        second = service(root, store).execute("MIG-001", PW)
        assert (first.attempt, second.attempt) == (1, 2)
        assert store.get("MIG-001").executions[PW] == [first, second]

    def test_warning_validation_may_run(self, root: Path) -> None:
        assert service(root, seeded_store(root, CheckStatus.WARN)).execute("MIG-001", PW).passed

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"generated": False}, "generate it first"),
            ({"validation": None}, "validate it first"),
            ({"validation": CheckStatus.FAIL}, "failed validation"),
        ],
    )
    def test_prerequisites(self, root: Path, kwargs: dict, message: str) -> None:
        process = FakeProcess()
        with pytest.raises(MigrationStateError, match=message):
            service(root, seeded_store(root, **kwargs), process).execute("MIG-001", PW)
        assert process.calls == []

    def test_project_outside_workspace_is_refused(self, root: Path, tmp_path: Path) -> None:
        store = seeded_store(root)
        record = store.get("MIG-001")
        moved = record.results[PW].model_copy(update={"output_dir": (tmp_path / "elsewhere").as_posix()})
        store.save(record.model_copy(update={"results": {PW: moved}}))
        with pytest.raises(MigrationStateError, match="outside the playwright workspace"):
            service(root, store).execute("MIG-001", PW)


class TestExecuteEndpoint:
    def _client(self, svc: ExecutionService) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_execution_service] = lambda: svc
        return TestClient(app)

    def test_returns_structured_result(self, root: Path) -> None:
        with self._client(service(root, seeded_store(root))) as client:
            response = client.post("/api/migration/execute", json={"migrationId": "MIG-001"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PASS" and body["exitCode"] == 0 and body["logs"]
        assert {"stdout", "stderr", "durationSeconds", "screenshots", "artifactsDir"} <= body.keys()

    def test_not_validated_is_a_conflict(self, root: Path) -> None:
        with self._client(service(root, seeded_store(root, validation=None))) as client:
            response = client.post("/api/migration/execute", json={"migrationId": "MIG-001"})
        assert response.status_code == 409

    def test_unknown_migration(self, root: Path) -> None:
        with self._client(service(root, InMemoryMigrationStore())) as client:
            assert client.post("/api/migration/execute", json={"migrationId": "MIG-404"}).status_code == 404

    def test_request_cannot_carry_commands(self, root: Path) -> None:
        with self._client(service(root, seeded_store(root))) as client:
            response = client.post(
                "/api/migration/execute", json={"migrationId": "MIG-001; rm -rf /", "targetFramework": "playwright"}
            )
        assert response.status_code == 422


def test_environment_is_not_mutated(root: Path) -> None:
    before = dict(os.environ)
    runner(root, FakeProcess()).run(generated_project(root))
    assert dict(os.environ) == before
