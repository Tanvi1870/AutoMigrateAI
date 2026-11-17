import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.dom_locators import locator_present, parse_dom, rank_candidates
from app.agents.repair_agent import (
    MIN_CANDIDATE_SCORE,
    REPAIR_TASK,
    RepairAgent,
    RepairNotPossibleError,
    _single_file_change,
    llm_context,
    with_locator,
)
from app.demo_app import _login_page
from app.dependencies import get_repair_service
from app.execution.engine import ExecutionEngine
from app.execution.playwright_runner import PlaywrightRunner
from app.execution.runner import ProcessOutput
from app.execution.selenium_runner import SeleniumRunner
from app.generators.playwright_generator import PlaywrightGenerator
from app.generators.selenium_generator import SeleniumGenerator
from app.llm.base import TASK_KEY, LLMError, LLMProvider
from app.llm.mock_provider import MockLLMProvider
from app.main import create_app
from app.models import (
    ArtifactKind,
    CanonicalTestCase,
    CheckStatus,
    ExecutionResult,
    ExecutionStatus,
    GeneratedArtifact,
    Locator,
    LocatorStrategy,
    MigrationRecord,
    MigrationResult,
    RepairChangeType,
    RepairContext,
    RepairStage,
    RepairStatus,
    Severity,
    StageStatus,
    TargetFramework,
    ValidationCheck,
    ValidationIssue,
)
from app.models.capabilities import LOCATOR_STRATEGIES
from app.models.enums import ApplicationType
from app.services.errors import MigrationStateError
from app.services.repair_service import RepairService, current_test_case
from app.services.validation_service import ValidationService
from app.models.demo import DemoFault
from app.storage.migration_store import InMemoryMigrationStore
from app.validation import ValidationEngine, Validator
from app.validation.engine import ValidationContext
from tests.factories import NOW, login_payload, make_execution, make_test_case
from tests.test_validation import ENGINE as VALIDATION, KEYWORDS, LOCATORS, TEMPLATES

PW, SE = TargetFramework.PLAYWRIGHT, TargetFramework.SELENIUM
GENERATORS = {
    PW: PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES),
    SE: SeleniumGenerator(KEYWORDS, LOCATORS, TEMPLATES),
}
WEB = LOCATOR_STRATEGIES[ApplicationType.WEB]
V1, V2 = _login_page("v1"), _login_page("v2")
LOGIN_BUTTON = make_test_case().object_repository["loginButton"]


class FakeBrowser:
    """Stands in for pytest + browser against the demo app: the click fails while the page object says loginBtn."""

    def __init__(self, html: str = V2, always_fail: bool = False) -> None:
        self.html, self.always_fail = html, always_fail
        self.runs = 0

    def __call__(self, command: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> ProcessOutput:
        self.runs += 1
        run_dir = Path(env["AM_ARTIFACTS_DIR"])
        broken = "loginBtn" in (cwd / "pages" / "login_page.py").read_text(encoding="utf-8") and "loginBtn" not in self.html
        if not (broken or self.always_fail):
            (run_dir / "junit.xml").write_text('<testsuites><testcase name="test_login"/></testsuites>', encoding="utf-8")
            return ProcessOutput(0, "1 passed", "")
        test_lines = (cwd / "tests" / "login_test.py").read_text(encoding="utf-8").splitlines()
        click_line = next(n for n, line in enumerate(test_lines, 1) if ".click()" in line)
        message = "playwright._impl._errors.TimeoutError: Locator.click: Timeout 10000ms exceeded."
        (run_dir / "junit.xml").write_text(
            f'<testsuites><testcase name="test_login"><failure message="{message}">'
            f"tests/login_test.py:{click_line}: TimeoutError</failure></testcase></testsuites>",
            encoding="utf-8",
        )
        (run_dir / "dom").mkdir()
        (run_dir / "dom" / "tests_login_test.py_test_login.html").write_text(self.html, encoding="utf-8")
        return ProcessOutput(1, "1 failed", "")


class FixedLLM(LLMProvider):
    name = "fixed"

    def __init__(self, reply: dict[str, Any] | None = None, error: bool = False) -> None:
        self.reply, self.error = reply, error
        self.contexts: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> str:
        self.contexts.append(context)
        if self.error:
            raise LLMError("offline")
        return json.dumps(self.reply)


def failed_execution(target: TargetFramework = PW, **overrides: Any) -> ExecutionResult:
    fields: dict[str, Any] = {
        "failed_step": 4,
        "error_type": "TimeoutError",
        "error_message": "Locator.click: Timeout 10000ms exceeded.",
        "target": target,
    }
    return make_execution(ExecutionStatus.FAIL, **(fields | overrides))


def context(html: str | None = V2, step: int = 4, test_case: CanonicalTestCase | None = None, **overrides: Any):
    test_case = test_case or make_test_case()
    the_step = next(s for s in test_case.steps if s.sequence == step)
    locator = test_case.object_repository.get(the_step.object_name or "")
    candidates = (
        rank_candidates(parse_dom(html), locator, the_step.object_name, test_case.object_repository, WEB)
        if html and locator
        else []
    )
    fields: dict[str, Any] = {
        "step": the_step,
        "locator": locator,
        "generated_code": "",
        "execution": failed_execution(failed_step=step),
        "object_repository": test_case.object_repository,
        "available_locators": candidates,
        "dom_snapshot": html,
    }
    return RepairContext(**(fields | overrides))


class TestDomLocators:
    def test_v2_button_is_the_top_candidate(self) -> None:
        candidates = rank_candidates(parse_dom(V2), LOGIN_BUTTON, "loginButton", make_test_case().object_repository, WEB)
        best = candidates[0]
        assert (best.strategy, best.value, best.tag, best.text) == (LocatorStrategy.DATA_TESTID, "login", "button", "Log in")
        assert best.score >= 0.9 and all(c.score < best.score for c in candidates[1:])
        assert [c.candidate_id for c in candidates] == list(range(len(candidates)))

    def test_failed_locator_presence(self) -> None:
        assert locator_present(parse_dom(V1), LOGIN_BUTTON) is True
        assert locator_present(parse_dom(V2), LOGIN_BUTTON) is False
        css = Locator(name="x", strategy=LocatorStrategy.CSS, value="#loginBtn")
        assert locator_present(parse_dom(V1), css) is None

    def test_labels_and_scripts(self) -> None:
        html = '<script>var x = "<button id=evil>";</script><label for="u">User name</label><input id="u">'
        elements = parse_dom(html)
        assert [(e.tag, e.attrs.get("id")) for e in elements] == [("label", None), ("input", "u")]
        assert elements[1].display_text == "User name"

    def test_locators_owned_by_other_objects_rank_lower(self) -> None:
        html = '<button id="submit">Log in</button><button data-testid="login">Log in</button>'
        repository = make_test_case().object_repository | {
            "other": Locator(name="other", strategy=LocatorStrategy.DATA_TESTID, value="login")
        }
        best = rank_candidates(parse_dom(html), LOGIN_BUTTON, "loginButton", repository, WEB)[0]
        assert best.label == "id=submit"


class TestRepairAgent:
    def test_locator_repair_with_mock_llm(self) -> None:
        proposal = RepairAgent(MockLLMProvider(), GENERATORS).propose(context(), make_test_case())
        assert proposal.change_type is RepairChangeType.LOCATOR and proposal.analysis_source == "mock"
        assert (proposal.old_value, proposal.new_value) == ("id=loginBtn", "data-testid=login")
        assert proposal.new_locator.source == "AI_REPAIRED" and proposal.new_locator.confidence == proposal.confidence
        assert proposal.affected_steps == [4] and proposal.file == "pages/login_page.py"
        assert proposal.changed_files == ["pages/login_page.py"]
        assert "LOGIN_BUTTON = '[data-testid=\"login\"]'" in proposal.repaired_code
        assert "LOGIN_BUTTON = '[id=\"loginBtn\"]'" in proposal.original_code
        changed = [l for l in proposal.code_diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        assert len(changed) == 4  # comment and constant, before and after
        assert 0.5 <= proposal.confidence <= 0.95
        assert proposal.diagnosis and proposal.root_cause and proposal.explanation and proposal.proposed_change

    def test_selenium_repair_changes_only_the_page_value(self) -> None:
        test_case = make_test_case()
        ctx = context(execution=failed_execution(SE, error_type="NoSuchElementException", error_message="no such element"))
        proposal = RepairAgent(None, GENERATORS).propose(ctx, test_case)
        assert "LOGIN_BUTTON = '[data-testid=\"login\"]'" in proposal.repaired_code
        assert proposal.analysis_source == "heuristic"
        assert proposal.changed_files == ["pages/login_page.py", "tests/login_test.py"]
        assert "-    driver.find_element(By.ID, LoginPage.LOGIN_BUTTON).click()" in proposal.code_diff
        assert "+    driver.find_element(By.CSS_SELECTOR, LoginPage.LOGIN_BUTTON).click()" in proposal.code_diff

    def test_test_intent_is_unchanged(self) -> None:
        test_case = make_test_case()
        proposal = RepairAgent(MockLLMProvider(), GENERATORS).propose(context(), test_case)
        before = {a.path: a.content for a in GENERATORS[PW].build(test_case)}
        repaired = with_locator(test_case, proposal.new_locator)
        after = {a.path: a.content for a in GENERATORS[PW].build(repaired)}
        assert repaired.steps == test_case.steps and repaired.test_data == test_case.test_data
        assert {p for p in before if before[p] != after[p]} == {"pages/login_page.py"}
        assert make_test_case().object_repository["loginButton"].value == "loginBtn"  # input untouched

    def test_llm_context_is_structured_and_codeless(self) -> None:
        llm = FixedLLM(error=True)
        RepairAgent(llm, GENERATORS).propose(context(), make_test_case())
        sent = llm.contexts[0]
        assert sent[TASK_KEY] == REPAIR_TASK and sent["failedLocator"] == "id=loginBtn"
        assert sent["candidates"][0]["locator"] == "data-testid=login"
        assert sent["objectRepository"]["loginButton"] == "id=loginBtn"

    def test_password_values_are_masked_for_the_llm(self) -> None:
        steps = login_payload()["steps"]
        steps[2]["value"] = "literal-secret"
        ctx = context(step=3, test_case=make_test_case(steps=steps))
        assert "literal-secret" not in json.dumps(llm_context(ctx))

    def test_llm_failure_falls_back_to_heuristic(self) -> None:
        proposal = RepairAgent(FixedLLM(error=True), GENERATORS).propose(context(), make_test_case())
        assert proposal.analysis_source == "heuristic" and "AI analysis unavailable" in proposal.explanation

    def test_llm_cannot_invent_a_locator(self) -> None:
        hostile = {
            "diagnosis": "d",
            "rootCause": "r",
            "candidateId": 99,
            "confidence": 1.0,
            "explanation": "use //*[@onclick]",
        }
        proposal = RepairAgent(FixedLLM(hostile), GENERATORS).propose(context(), make_test_case())
        assert proposal.new_value == "data-testid=login" and proposal.analysis_source == "heuristic"
        assert "ignored" in proposal.explanation

    def test_llm_confidence_is_capped(self) -> None:
        reply = {"diagnosis": "d", "rootCause": "r", "candidateId": 0, "confidence": 1.0, "explanation": "e"}
        assert RepairAgent(FixedLLM(reply), GENERATORS).propose(context(), make_test_case()).confidence == 0.95

    def test_llm_may_decline(self) -> None:
        reply = {"diagnosis": "Nothing similar", "rootCause": "r", "candidateId": None, "confidence": 0, "explanation": "e"}
        with pytest.raises(RepairNotPossibleError, match="no matching element"):
            RepairAgent(FixedLLM(reply), GENERATORS).propose(context(), make_test_case())

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"execution": failed_execution(error_type="AssertionError", error_message="assert 'Hi' == 'Welcome'")}, "intent"),
            ({"execution": make_execution(ExecutionStatus.TIMEOUT, failed_step=4)}, "timed out"),
            ({"dom_snapshot": V1}, "still matches the page"),
            ({"dom_snapshot": None, "available_locators": []}, "without guessing"),
        ],
        ids=["assertion", "timeout", "locator-present", "no-dom"],
    )
    def test_refuses_repairs_that_would_guess_or_change_intent(self, overrides: dict, message: str) -> None:
        with pytest.raises(RepairNotPossibleError, match=message):
            RepairAgent(MockLLMProvider(), GENERATORS).propose(context(**overrides), make_test_case())

    def test_nothing_similar_on_the_page(self) -> None:
        html = '<p id="footer">Copyright</p>'
        with pytest.raises(RepairNotPossibleError, match="resembles"):
            RepairAgent(None, GENERATORS).propose(context(html), make_test_case())

    def test_a_real_llm_is_reported_as_llm(self) -> None:
        reply = {"diagnosis": "d", "rootCause": "r", "candidateId": 0, "confidence": 0.8, "explanation": "e"}
        assert RepairAgent(FixedLLM(reply), GENERATORS).propose(context(), make_test_case()).analysis_source == "llm"

    def test_assertion_steps_are_never_retargeted(self) -> None:
        # Step 5 asserts the welcome message; it is missing from the page (V2 login page), so a "similar"
        # element could be picked and the test would pass without checking what it was written to check.
        ctx = context(V2, step=5, execution=failed_execution(failed_step=5))
        with pytest.raises(RepairNotPossibleError, match="is an assertion"):
            RepairAgent(MockLLMProvider(), GENERATORS).propose(ctx, make_test_case())

    def test_assertion_failure_mentioning_the_locator_is_not_a_locator_problem(self) -> None:
        # Playwright's expect() failures include "waiting for locator" in the call log.
        message = "AssertionError: Locator expected to have text 'Welcome'\nCall log:\n  - waiting for locator(\"#x\")"
        ctx = context(execution=failed_execution(error_type="AssertionError", error_message=message))
        with pytest.raises(RepairNotPossibleError, match="not a missing element"):
            RepairAgent(MockLLMProvider(), GENERATORS).propose(ctx, make_test_case())

    def test_css_and_xpath_locators_need_a_human(self) -> None:
        test_case = with_locator(make_test_case(), Locator(name="loginButton", strategy=LocatorStrategy.CSS, value="#loginBtn"))
        ctx = context(V2, test_case=test_case)
        with pytest.raises(RepairNotPossibleError, match="cannot be checked against the page snapshot"):
            RepairAgent(MockLLMProvider(), GENERATORS).propose(ctx, test_case)

    def test_llm_cannot_pick_a_dissimilar_element(self) -> None:
        ctx = context()
        weakest = min(ctx.available_locators, key=lambda c: c.score)
        assert weakest.score < MIN_CANDIDATE_SCORE  # precondition: the page has an unrelated element
        reply = {"diagnosis": "d", "rootCause": "r", "candidateId": weakest.candidate_id, "confidence": 0.9, "explanation": "e"}
        proposal = RepairAgent(FixedLLM(reply), GENERATORS).propose(ctx, make_test_case())
        assert proposal.analysis_source == "heuristic" and proposal.new_value == "data-testid=login"
        assert "ignored" in proposal.explanation

    def test_wait_repair_when_element_is_present(self) -> None:
        test_case = make_test_case(steps=login_payload()["steps"] + [{"sequence": 6, "keyword": "web_wait", "object": "loginButton"}])
        ctx = context(V1, step=6, test_case=test_case)
        proposal = RepairAgent(MockLLMProvider(), GENERATORS).propose(ctx, test_case)
        assert proposal.change_type is RepairChangeType.WAIT and proposal.file == "tests/login_test.py"
        assert (proposal.old_value, proposal.new_value, proposal.affected_steps) == ("", "60000", [6])
        assert "timeout=60000" in proposal.repaired_code

    def test_multi_file_or_unrelated_changes_are_rejected(self) -> None:
        def art(path: str, content: str, kind: ArtifactKind = ArtifactKind.PAGE_OBJECT) -> GeneratedArtifact:
            return GeneratedArtifact(path=path, kind=kind, content=content)

        page = "class P:\n    # loginButton: id=a\n    LOGIN_BUTTON = 'a'\n    OTHER = 'x'\n"
        with pytest.raises(RepairNotPossibleError, match="only one"):
            _single_file_change(
                [art("pages/p.py", page), art("tests/t.py", "a", ArtifactKind.TEST)],
                [art("pages/p.py", page.replace("'a'", "'b'")), art("tests/t.py", "b", ArtifactKind.TEST)],
                ArtifactKind.PAGE_OBJECT,
                "loginButton",
            )
        with pytest.raises(RepairNotPossibleError, match="unrelated"):
            _single_file_change(
                [art("pages/p.py", page)], [art("pages/p.py", page.replace("class P:", "class Q:"))],
                ArtifactKind.PAGE_OBJECT, "loginButton",
            )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "generated"


class Workspace:
    """A generated project with one failed run, recorded like the real pipeline would."""

    def __init__(self, root: Path, browser: FakeBrowser, target: TargetFramework = PW) -> None:
        self.root, self.target, self.browser = root, target, browser
        self.project = root / target / "MIG-001"
        test_case = make_test_case()
        artifacts = GENERATORS[target].generate(test_case, self.project)
        runner_cls = PlaywrightRunner if target is PW else SeleniumRunner
        self.execution = ExecutionEngine({target: runner_cls(root, 60, True, process_runner=browser)})
        failure = self.execution.execute(target, self.project)
        assert not failure.passed, "the fixture should start from a failed run"
        self.store = InMemoryMigrationStore()
        self.store.save(
            MigrationRecord(
                migration_id="MIG-001",
                created_at=NOW,
                test_case=test_case,
                results={
                    target: MigrationResult(
                        migration_id="MIG-001",
                        test_case_id=test_case.test_case_id,
                        target_framework=target,
                        output_dir=self.project.as_posix(),
                        artifacts=artifacts,
                        generated_at=NOW,
                    )
                },
                validations={target: VALIDATION.validate_artifacts(artifacts, test_case, target)},
                executions={target: [failure]},
            )
        )

    def service(self, llm: LLMProvider | None = None, validation: ValidationEngine = VALIDATION) -> RepairService:
        return RepairService(
            self.store, RepairAgent(llm or MockLLMProvider(), GENERATORS), GENERATORS, validation, self.execution, lambda: NOW
        )

    def page_source(self) -> str:
        return (self.project / "pages" / "login_page.py").read_text(encoding="utf-8")


class TestRepairService:
    def test_propose_only_changes_nothing(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser())
        before = ws.page_source()
        repair = ws.service().repair("MIG-001", PW, apply=False)
        assert repair.status is RepairStatus.PROPOSED and repair.re_execution is None
        assert ws.page_source() == before and ws.browser.runs == 1
        assert ws.service().history("MIG-001") == [repair]

    @pytest.mark.parametrize("target", [PW, SE])
    def test_apply_validates_reruns_and_records(self, root: Path, target: TargetFramework) -> None:
        ws = Workspace(root, FakeBrowser(), target)
        repair = ws.service().repair("MIG-001", target, apply=True)
        assert repair.status is RepairStatus.PASSED_AFTER_REPAIR and not repair.rolled_back
        assert repair.validation_status is CheckStatus.WARN  # AI-repaired locators are flagged for review
        assert repair.re_execution.passed and repair.re_execution.attempt == 2
        assert repair.outcome == "FAIL at step 4 -> PASS"
        assert "data-testid" in ws.page_source() and ws.browser.runs == 2

        record = ws.store.get("MIG-001")
        assert record.repairs == [repair] and len(record.executions[target]) == 2
        assert record.test_case.object_repository["loginButton"].value == "loginBtn"  # the source never changes
        assert current_test_case(record, target).object_repository["loginButton"].value == "login"
        repaired_page = next(a for a in record.results[target].artifacts if a.kind is ArtifactKind.PAGE_OBJECT)
        assert repaired_page.content == ws.page_source()
        assert current_test_case(record, SE if target is PW else PW) == record.test_case

    def test_timeline_for_the_ui(self, root: Path) -> None:
        repair = Workspace(root, FakeBrowser()).service().repair("MIG-001", PW, apply=True)
        stages = [(t.stage, t.status) for t in repair.timeline]
        assert stages == [
            (RepairStage.ORIGINAL, StageStatus.DONE),
            (RepairStage.FAILURE, StageStatus.FAIL),
            (RepairStage.AI_ANALYSIS, StageStatus.DONE),
            (RepairStage.REPAIR, StageStatus.DONE),
            (RepairStage.VALIDATION, StageStatus.WARNING),
            (RepairStage.RERUN, StageStatus.PASS),
            (RepairStage.RESULT, StageStatus.PASS),
        ]
        dumped = repair.model_dump(mode="json", by_alias=True)
        assert [t["title"] for t in dumped["timeline"]] == [
            "Original", "Failure", "AI Analysis", "Repair", "Validation", "Re-run", "PASS"
        ]
        assert dumped["timeline"][0]["details"] == ["Step 4: web_click loginButton", "Locator id=loginBtn"]
        assert "id=loginBtn -> data-testid=login" in dumped["timeline"][3]["details"]

    def test_failed_rerun_restores_the_original_code(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser(always_fail=True))
        original = ws.page_source()
        repair = ws.service().repair("MIG-001", PW, apply=True)
        assert repair.status is RepairStatus.FAILED and repair.rolled_back
        assert ws.page_source() == original
        record = ws.store.get("MIG-001")
        assert len(record.executions[PW]) == 2 and current_test_case(record, PW) == record.test_case
        assert [t.status for t in repair.timeline][-2:] == [StageStatus.FAIL, StageStatus.FAIL]

    def test_repair_failing_validation_is_rejected_and_not_applied(self, root: Path) -> None:
        class AlwaysFails(Validator):
            checks = (ValidationCheck.STANDARDS,)

            def validate(self, context: ValidationContext) -> list[ValidationIssue]:
                return [ValidationIssue(check=ValidationCheck.STANDARDS, severity=Severity.ERROR, message="no")]

        ws = Workspace(root, FakeBrowser())
        original = ws.page_source()
        repair = ws.service(validation=ValidationEngine([AlwaysFails()])).repair("MIG-001", PW, apply=True)
        assert repair.status is RepairStatus.REJECTED and repair.validation_status is CheckStatus.FAIL
        assert ws.page_source() == original and ws.browser.runs == 1
        assert [t.status for t in repair.timeline][4:] == [StageStatus.FAIL, StageStatus.SKIPPED, StageStatus.FAIL]

    def test_prerequisites(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser())
        record = ws.store.get("MIG-001")
        with pytest.raises(MigrationStateError, match="generate it first"):
            ws.service().repair("MIG-001", SE, apply=False)
        ws.store.save(record.model_copy(update={"executions": {}}))
        with pytest.raises(MigrationStateError, match="not been run"):
            ws.service().repair("MIG-001", PW, apply=False)
        ws.store.save(record.model_copy(update={"executions": {PW: [make_execution()]}}))
        with pytest.raises(MigrationStateError, match="nothing to repair"):
            ws.service().repair("MIG-001", PW, apply=False)
        ws.store.save(record.model_copy(update={"executions": {PW: [failed_execution(failed_step=None)]}}))
        with pytest.raises(RepairNotPossibleError, match="traced to a test step"):
            ws.service().repair("MIG-001", PW, apply=False)

    def test_revalidation_after_a_verified_repair_checks_the_repaired_test(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser())
        ws.service().repair("MIG-001", PW, apply=True)
        validation = ValidationService(ws.store, VALIDATION).validate("MIG-001", PW)
        assert validation.locators is CheckStatus.WARN and validation.status is CheckStatus.WARN
        assert not [issue for issue in validation.issues if issue.severity is Severity.ERROR]


class TestDemoFaultReplay:
    """current_test_case() layers a DEMO MODE fault and verified repairs in the order they happened."""

    @staticmethod
    def fault(repairs_before: int) -> DemoFault:
        return DemoFault(
            step_sequence=4,
            keyword="web_click",
            object_name="loginButton",
            original_locator=LOGIN_BUTTON,
            injected_locator=Locator(name="loginButton", strategy=LocatorStrategy.ID, value="am-demo-missing-loginButton"),
            description="demo",
            repairs_before=repairs_before,
        )

    def test_fault_alone_breaks_only_its_target(self) -> None:
        record = MigrationRecord(
            migration_id="MIG-001", created_at=NOW, test_case=make_test_case(), demo_faults={PW: self.fault(0)}
        )
        assert current_test_case(record, PW).object_repository["loginButton"].value == "am-demo-missing-loginButton"
        assert current_test_case(record, SE) == record.test_case
        assert record.test_case.object_repository["loginButton"].value == "loginBtn"

    def test_repair_after_the_fault_wins_and_a_later_fault_wins_again(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser())
        ws.service().repair("MIG-001", PW, apply=True)
        record = ws.store.get("MIG-001")
        repaired_after = record.model_copy(update={"demo_faults": {PW: self.fault(0)}})
        assert current_test_case(repaired_after, PW).object_repository["loginButton"].value == "login"
        fault_after = record.model_copy(update={"demo_faults": {PW: self.fault(1)}})
        assert current_test_case(fault_after, PW).object_repository["loginButton"].value == "am-demo-missing-loginButton"


class TestRepairEndpoints:
    def _client(self, service: RepairService) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_repair_service] = lambda: service
        return TestClient(app)

    def test_repair_and_history(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser())
        with self._client(ws.service()) as client:
            response = client.post("/api/migration/repair", json={"migrationId": "MIG-001", "apply": True})
            history = client.get("/api/migration/MIG-001/repairs")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PASSED_AFTER_REPAIR" and body["outcome"] == "FAIL at step 4 -> PASS"
        assert {"diagnosis", "rootCause", "proposedChange", "oldValue", "newValue", "repairedCode", "confidence",
                "explanation", "codeDiff"} <= body["proposal"].keys()
        assert history.status_code == 200 and [r["status"] for r in history.json()] == ["PASSED_AFTER_REPAIR"]

    def test_unrepairable_failure_is_422(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser(html='<p id="footer">x</p>'))
        with self._client(ws.service(llm=FixedLLM(error=True))) as client:
            response = client.post("/api/migration/repair", json={"migrationId": "MIG-001"})
        assert response.status_code == 422 and "resembles" in response.json()["message"]

    def test_unknown_migration_is_404(self, root: Path) -> None:
        ws = Workspace(root, FakeBrowser())
        with self._client(ws.service()) as client:
            assert client.post("/api/migration/repair", json={"migrationId": "MIG-404"}).status_code == 404
            assert client.get("/api/migration/MIG-404/repairs").status_code == 404
