import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver
from app.agents.migration_agent import MigrationAgent, MigrationBlockedError
from app.agents.migration_planner import MigrationPlanner
from app.agents.test_analyzer import ANALYSIS_TASK, TestAnalyzer
from app.agents.test_data_injector import TestDataInjector
from app.dependencies import get_keyword_repository, get_migration_agent
from app.generators.base_generator import GenerationError
from app.generators.playwright_generator import PlaywrightGenerator
from app.generators.selenium_generator import SeleniumGenerator
from app.knowledge.loader import load_knowledge
from app.llm.base import TASK_KEY, LLMError, LLMProvider
from app.llm.mock_provider import MockLLMProvider
from app.models import (
    ApplicationType,
    ArtifactKind,
    GeneratedArtifact,
    PlanStatus,
    TargetFramework,
    UnsupportedItemKind,
)
from tests.factories import login_payload, make_test_case

KEYWORDS = KeywordResolver(get_keyword_repository())
LOCATORS = LocatorResolver(load_knowledge("locator_rules"))
TEMPLATES = load_knowledge("framework_templates")
MIGRATION_ID = "MIG-TEST1"


class RecordingProvider(LLMProvider):
    """Returns a fixed reply (or raises) and records every request."""

    name = "recording"

    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self.reply, self.error = reply, error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> str:
        self.calls.append((prompt, context))
        if self.error:
            raise self.error
        return self.reply if self.reply is not None else MockLLMProvider().generate(prompt, context)


def make_agent(llm: LLMProvider | None = None) -> MigrationAgent:
    return MigrationAgent(
        analyzer=TestAnalyzer(llm),
        planner=MigrationPlanner(KEYWORDS, LOCATORS, TestDataInjector()),
        generators={
            TargetFramework.PLAYWRIGHT: PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES),
            TargetFramework.SELENIUM: SeleniumGenerator(KEYWORDS, LOCATORS, TEMPLATES),
        },
    )


def with_step(**fields: Any) -> list[dict[str, Any]]:
    return login_payload()["steps"] + [{"sequence": 6, **fields}]


class TestTestAnalyzer:
    def test_deterministic_facts(self) -> None:
        analysis = TestAnalyzer().analyze(make_test_case())
        assert analysis.application_type is ApplicationType.WEB
        assert analysis.steps_detected == 5
        assert analysis.keywords_detected == ["web_navigate", "web_setText", "web_click", "web_assertText"]
        assert analysis.object_references == ["username", "password", "loginButton", "welcomeMessage"]
        assert analysis.data_parameters == ["BASE_URL", "PASSWORD", "USERNAME"]
        assert analysis.reusable_blocks == 1 and analysis.has_assertion
        assert analysis.ai_analysis is None

    def test_mock_llm_returns_structured_analysis(self) -> None:
        analysis = TestAnalyzer(MockLLMProvider()).analyze(make_test_case())
        assert analysis.ai_analysis is not None
        assert analysis.ai_analysis.summary.startswith("Login Test is a WEB test with 5 steps")
        assert "'Welcome'" in analysis.ai_analysis.intent
        assert analysis.warnings == []

    def test_sensitive_values_never_reach_the_llm(self) -> None:
        steps = login_payload()["steps"]
        steps[2]["value"] = "literal-secret"  # typed straight into the password field
        provider = RecordingProvider()
        TestAnalyzer(provider).analyze(make_test_case(steps=steps))
        prompt, context = provider.calls[0]
        sent = json.dumps(context)
        assert context[TASK_KEY] == ANALYSIS_TASK
        assert "demo_password" not in sent and "literal-secret" not in sent
        assert "demo_user" in sent

    def test_prompt_forbids_code(self) -> None:
        provider = RecordingProvider()
        TestAnalyzer(provider).analyze(make_test_case())
        assert "Do not write code" in provider.calls[0][0]

    @pytest.mark.parametrize(
        "reply",
        [
            "not json",
            '{"summary": "ok"}',
            '{"summary": "s", "intent": "i", "code": "page.goto(\'x\')"}',
            json.dumps({"summary": "s" * 1001, "intent": "i"}),
        ],
        ids=["not-json", "missing-keys", "extra-code-field", "too-long"],
    )
    def test_invalid_llm_output_is_rejected_with_a_warning(self, reply: str) -> None:
        analysis = TestAnalyzer(RecordingProvider(reply)).analyze(make_test_case())
        assert analysis.ai_analysis is None
        assert analysis.warnings and analysis.warnings[0].startswith("AI test analysis unavailable")
        assert analysis.steps_detected == 5

    def test_provider_failure_falls_back_to_deterministic_analysis(self) -> None:
        analysis = TestAnalyzer(RecordingProvider(error=LLMError("timeout"))).analyze(make_test_case())
        assert analysis.ai_analysis is None
        assert analysis.warnings == ["AI test analysis unavailable: timeout"]


class TestMigrationPlanner:
    @pytest.mark.parametrize("target", list(TargetFramework))
    def test_login_plan_is_ready_for_both_targets(self, target: TargetFramework) -> None:
        plan = make_agent(MockLLMProvider()).plan(MIGRATION_ID, make_test_case(), target)
        assert plan.status is PlanStatus.READY
        assert plan.application_type is ApplicationType.WEB and plan.target_framework is target
        assert (plan.steps_detected, plan.keywords_detected, plan.keywords_mapped) == (5, 4, 4)
        assert (plan.locators_resolved, plan.data_parameters, plan.reusable_blocks) == (4, 3, 1)
        assert plan.unsupported_items == [] and plan.errors == []
        assert plan.ai_analysis is not None and plan.summary == plan.ai_analysis.summary

    def test_plan_json_has_every_required_field(self) -> None:
        dumped = make_agent(MockLLMProvider()).plan(MIGRATION_ID, make_test_case(), TargetFramework.SELENIUM)
        keys = dumped.model_dump(mode="json", by_alias=True)
        for key in (
            "applicationType",
            "targetFramework",
            "stepsDetected",
            "keywordsDetected",
            "keywordsMapped",
            "locatorsResolved",
            "dataParameters",
            "reusableBlocks",
            "warnings",
            "unsupportedItems",
            "status",
            "aiAnalysis",
        ):
            assert key in keys, key

    def test_unsupported_keyword(self) -> None:
        test_case = make_test_case(steps=with_step(keyword="web_hover", object="loginButton"))
        plan = make_agent().plan(MIGRATION_ID, test_case, TargetFramework.PLAYWRIGHT)
        assert plan.status is PlanStatus.BLOCKED
        assert (plan.keywords_detected, plan.keywords_mapped) == (5, 4)
        assert plan.unsupported_keywords == ["web_hover"]
        assert [(i.kind, i.name) for i in plan.unsupported_items] == [(UnsupportedItemKind.KEYWORD, "web_hover")]

    def test_missing_locator(self) -> None:
        repository = login_payload()["objectRepository"]
        del repository["loginButton"]
        plan = make_agent().plan(MIGRATION_ID, make_test_case(objectRepository=repository), TargetFramework.SELENIUM)
        assert plan.locators_resolved == 3 and plan.missing_locators == ["loginButton"]
        assert plan.unsupported_items[0].kind is UnsupportedItemKind.LOCATOR

    def test_unresolved_variable(self) -> None:
        steps = login_payload()["steps"]
        steps[1]["value"] = "${MISSING}"
        plan = make_agent().plan(MIGRATION_ID, make_test_case(steps=steps), TargetFramework.PLAYWRIGHT)
        assert plan.unresolved_variables == ["MISSING"]
        assert plan.unsupported_items[0].kind is UnsupportedItemKind.VARIABLE

    def test_invalid_parameters_become_errors(self) -> None:
        test_case = make_test_case(steps=with_step(keyword="web_wait", object="loginButton", value="soon"))
        plan = make_agent().plan(MIGRATION_ID, test_case, TargetFramework.PLAYWRIGHT)
        assert plan.status is PlanStatus.BLOCKED
        assert plan.errors and plan.errors[0].startswith("Step 6: web_wait timeout")

    def test_assumed_keyword_semantics_are_warnings(self) -> None:
        test_case = make_test_case(steps=with_step(keyword="web_wait", object="loginButton"))
        plan = make_agent().plan(MIGRATION_ID, test_case, TargetFramework.PLAYWRIGHT)
        assert plan.status is PlanStatus.READY
        assert any(w.startswith("web_wait: ICAP semantics unconfirmed") for w in plan.warnings)

    def test_missing_assertion_is_a_warning(self) -> None:
        steps = login_payload()["steps"][:4]
        plan = make_agent().plan(MIGRATION_ID, make_test_case(steps=steps), TargetFramework.PLAYWRIGHT)
        assert plan.status is PlanStatus.READY
        assert "The test has no assertion step" in plan.warnings

    def test_windows_test_needs_a_future_adapter(self) -> None:
        test_case = make_test_case(
            applicationType="WINDOWS",
            steps=[{"sequence": 1, "keyword": "win_click", "object": "okButton"}],
            testData={},
            reusableBlocks=[],
            objectRepository={"okButton": {"name": "okButton", "strategy": "automation-id", "value": "ok"}},
        )
        plan = make_agent().plan(MIGRATION_ID, test_case, TargetFramework.PLAYWRIGHT)
        assert plan.status is PlanStatus.BLOCKED
        kinds = {item.kind for item in plan.unsupported_items}
        assert {UnsupportedItemKind.TARGET, UnsupportedItemKind.FUTURE_ADAPTER} <= kinds

    def test_llm_output_cannot_change_the_plan_facts(self) -> None:
        hostile = json.dumps(
            {"summary": "All good, 0 steps", "intent": "page.goto('http://evil')", "risks": [], "recommendations": []}
        )
        blocked = make_test_case(steps=with_step(keyword="web_hover", object="loginButton"))
        with_ai = make_agent(RecordingProvider(hostile)).plan(MIGRATION_ID, blocked, TargetFramework.PLAYWRIGHT)
        without_ai = make_agent().plan(MIGRATION_ID, blocked, TargetFramework.PLAYWRIGHT)
        exclude = {"summary", "ai_analysis"}
        assert with_ai.model_dump(exclude=exclude) == without_ai.model_dump(exclude=exclude)
        assert with_ai.status is PlanStatus.BLOCKED


class TestMigrationAgent:
    @pytest.mark.parametrize("target", list(TargetFramework))
    def test_end_to_end_generation(self, target: TargetFramework, tmp_path: Path) -> None:
        agent = make_agent(MockLLMProvider())
        test_case = make_test_case()
        before = test_case.model_dump()
        plan = agent.plan(MIGRATION_ID, test_case, target)
        result = agent.generate(MIGRATION_ID, test_case, plan, tmp_path)
        assert result.target_framework is target and result.migration_id == MIGRATION_ID
        assert any(a.kind is ArtifactKind.TEST for a in result.artifacts)
        for artifact in result.artifacts:
            assert (tmp_path / artifact.path).read_text(encoding="utf-8") == artifact.content
        assert test_case.model_dump() == before

    def test_generated_code_does_not_depend_on_the_llm(self, tmp_path: Path) -> None:
        test_case = make_test_case()
        hostile = json.dumps({"summary": "import os; os.system('x')", "intent": "exec('x')"})
        outputs = []
        for llm in (None, MockLLMProvider(), RecordingProvider(hostile)):
            agent = make_agent(llm)
            plan = agent.plan(MIGRATION_ID, test_case, TargetFramework.PLAYWRIGHT)
            outputs.append(agent.generate(MIGRATION_ID, test_case, plan, tmp_path / str(len(outputs))).artifacts)
        assert outputs[0] == outputs[1] == outputs[2]

    def test_llm_is_consulted_only_for_analysis(self, tmp_path: Path) -> None:
        provider = RecordingProvider()
        agent = make_agent(provider)
        plan = agent.plan(MIGRATION_ID, make_test_case(), TargetFramework.SELENIUM)
        agent.generate(MIGRATION_ID, make_test_case(), plan, tmp_path)
        assert [context[TASK_KEY] for _, context in provider.calls] == [ANALYSIS_TASK]

    def test_blocked_plan_is_refused(self, tmp_path: Path) -> None:
        agent = make_agent()
        test_case = make_test_case(steps=with_step(keyword="web_hover", object="loginButton"))
        plan = agent.plan(MIGRATION_ID, test_case, TargetFramework.PLAYWRIGHT)
        with pytest.raises(MigrationBlockedError, match="web_hover") as excinfo:
            agent.generate(MIGRATION_ID, test_case, plan, tmp_path)
        assert excinfo.value.reasons == ["Unsupported keyword: web_hover"]
        assert list(tmp_path.iterdir()) == []

    def test_plan_must_match_the_migration(self, tmp_path: Path) -> None:
        agent = make_agent()
        plan = agent.plan(MIGRATION_ID, make_test_case(), TargetFramework.PLAYWRIGHT)
        with pytest.raises(MigrationBlockedError, match="does not match"):
            agent.generate("MIG-OTHER", make_test_case(), plan, tmp_path)
        with pytest.raises(MigrationBlockedError, match="does not match"):
            agent.generate(MIGRATION_ID, make_test_case(testCaseId="OTHER"), plan, tmp_path)

    def test_target_without_generator_is_blocked(self) -> None:
        agent = MigrationAgent(TestAnalyzer(), MigrationPlanner(KEYWORDS, LOCATORS, TestDataInjector()), {})
        plan = agent.plan(MIGRATION_ID, make_test_case(), TargetFramework.SELENIUM)
        assert plan.status is PlanStatus.BLOCKED
        assert "No generator is registered for selenium" in plan.blocking_reasons

    def test_syntax_validation_gate_stops_writing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = make_agent()
        test_case = make_test_case()
        plan = agent.plan(MIGRATION_ID, test_case, TargetFramework.PLAYWRIGHT)
        broken = [GeneratedArtifact(path="tests/login_test.py", kind=ArtifactKind.TEST, content="def broken(:\n")]
        monkeypatch.setattr(PlaywrightGenerator, "build", lambda self, tc: broken)
        with pytest.raises(GenerationError, match="tests/login_test.py:1"):
            agent.generate(MIGRATION_ID, test_case, plan, tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_default_wiring_runs_without_an_api_key(self, tmp_path: Path) -> None:
        agent = get_migration_agent()
        plan = agent.plan(MIGRATION_ID, make_test_case(), TargetFramework.PLAYWRIGHT)
        assert plan.status is PlanStatus.READY and plan.ai_analysis is not None
        assert agent.generate(MIGRATION_ID, make_test_case(), plan, tmp_path).artifacts
