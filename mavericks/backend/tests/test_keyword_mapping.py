import ast
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.keyword_resolver import (
    KeywordResolver,
    RenderContext,
    TemplateRenderError,
    UnsupportedKeywordError,
)
from app.config import PROJECT_ROOT
from app.dependencies import get_parser
from app.knowledge.keyword_repository import KeywordRepository
from app.knowledge.loader import load_knowledge
from app.models import (
    ApplicationType,
    KeywordResolution,
    ParameterIssueCode,
    ResolutionStatus,
    TargetFramework,
    TestStep,
    UnsupportedReason,
)

REPOSITORY = KeywordRepository.load()
RESOLVER = KeywordResolver(REPOSITORY)
PW, SE = TargetFramework.PLAYWRIGHT, TargetFramework.SELENIUM
CONTEXTS = {
    PW: RenderContext(locator='[id="loginBtn"]'),
    SE: RenderContext(by="ID", locator_value="loginBtn"),
}
FIXTURE_NAMES = {"page", "driver"}

# keyword, step fields, expected Playwright code, expected Selenium code
RENDER_CASES = [
    (
        "web_navigate",
        {"value": "https://example.test/login"},
        "page.goto('https://example.test/login')",
        "driver.get('https://example.test/login')",
    ),
    (
        "web_click",
        {"object_name": "loginButton"},
        "page.locator('[id=\"loginBtn\"]').click()",
        "driver.find_element(By.ID, 'loginBtn').click()",
    ),
    (
        "web_setText",
        {"object_name": "loginButton", "value": "demo_user"},
        "page.locator('[id=\"loginBtn\"]').fill('demo_user')",
        "driver.find_element(By.ID, 'loginBtn').clear()\ndriver.find_element(By.ID, 'loginBtn').send_keys('demo_user')",
    ),
    (
        "web_getText",
        {"object_name": "loginButton", "value": "order_total"},
        "order_total = page.locator('[id=\"loginBtn\"]').inner_text()",
        "order_total = driver.find_element(By.ID, 'loginBtn').text",
    ),
    (
        "web_assertText",
        {"object_name": "loginButton", "expected": "Welcome"},
        "expect(page.locator('[id=\"loginBtn\"]')).to_have_text('Welcome')",
        "assert driver.find_element(By.ID, 'loginBtn').text == 'Welcome'",
    ),
    (
        "web_wait",
        {"object_name": "loginButton", "value": "5000"},
        "page.locator('[id=\"loginBtn\"]').wait_for(state=\"visible\", timeout=5000)",
        "WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, 'loginBtn')))",
    ),
    (
        "web_select",
        {"object_name": "loginButton", "value": "IN"},
        "page.locator('[id=\"loginBtn\"]').select_option('IN')",
        "Select(driver.find_element(By.ID, 'loginBtn')).select_by_value('IN')",
    ),
]
STEP_FIELDS = {keyword: fields for keyword, fields, _, _ in RENDER_CASES}


def make_step(keyword: str, sequence: int = 1, **fields: Any) -> TestStep:
    return TestStep(sequence=sequence, keyword=keyword, **fields)


def valid_step(keyword: str) -> TestStep:
    return make_step(keyword, **STEP_FIELDS[keyword])


def imported_names(imports: list[str]) -> set[str]:
    names: set[str] = set()
    for node in ast.parse("\n".join(imports)).body:
        assert isinstance(node, ast.Import | ast.ImportFrom)
        names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
    return names


def free_names(code: str) -> set[str]:
    nodes = [n for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Name)]
    stored = {n.id for n in nodes if isinstance(n.ctx, ast.Store)}
    return {n.id for n in nodes if isinstance(n.ctx, ast.Load)} - stored


class TestSupportedKeywords:
    def test_render_cases_cover_every_supported_keyword(self) -> None:
        assert set(STEP_FIELDS) == set(REPOSITORY.keywords)

    @pytest.mark.parametrize("keyword", [case[0] for case in RENDER_CASES])
    @pytest.mark.parametrize("target", [PW, SE])
    def test_resolves_from_knowledge_base(self, keyword: str, target: TargetFramework) -> None:
        resolution = RESOLVER.resolve(keyword, target)
        definition = REPOSITORY.get(keyword)

        assert definition is not None
        assert resolution.status is ResolutionStatus.RESOLVED
        assert resolution.template == definition.targets[target].python
        assert resolution.imports == definition.targets[target].imports
        assert resolution.description == definition.description
        assert resolution.application_type is ApplicationType.WEB
        assert resolution.assumption == definition.assumption
        assert resolution.reason is None and resolution.issues == []

    @pytest.mark.parametrize(("keyword", "fields", "playwright", "selenium"), RENDER_CASES, ids=[c[0] for c in RENDER_CASES])
    def test_renders_expected_playwright_code(self, keyword: str, fields: dict, playwright: str, selenium: str) -> None:
        assert RESOLVER.render(make_step(keyword, **fields), PW, CONTEXTS[PW]) == playwright

    @pytest.mark.parametrize(("keyword", "fields", "playwright", "selenium"), RENDER_CASES, ids=[c[0] for c in RENDER_CASES])
    def test_renders_expected_selenium_code(self, keyword: str, fields: dict, playwright: str, selenium: str) -> None:
        assert RESOLVER.render(make_step(keyword, **fields), SE, CONTEXTS[SE]) == selenium

    @pytest.mark.parametrize("keyword", [case[0] for case in RENDER_CASES])
    @pytest.mark.parametrize("target", [PW, SE])
    def test_rendered_code_is_valid_python_with_matching_imports(self, keyword: str, target: TargetFramework) -> None:
        resolution = RESOLVER.resolve(keyword, target)
        code = RESOLVER.render(valid_step(keyword), target, CONTEXTS[target])

        names = free_names(code)
        imports = imported_names(resolution.imports)
        assert names <= imports | FIXTURE_NAMES, f"undeclared names: {names - imports - FIXTURE_NAMES}"
        assert imports <= names, f"unused imports: {imports - names}"

    def test_web_wait_uses_default_timeout_when_value_is_omitted(self) -> None:
        step = make_step("web_wait", object_name="loginButton")

        assert RESOLVER.resolve_step(step, PW).is_resolved
        assert RESOLVER.render(step, PW, CONTEXTS[PW]).endswith("timeout=30000)")
        assert "WebDriverWait(driver, 30)" in RESOLVER.render(step, SE, CONTEXTS[SE])

    def test_set_text_with_empty_value_clears_the_field(self) -> None:
        step = make_step("web_setText", object_name="loginButton", value="")

        assert RESOLVER.render(step, PW, CONTEXTS[PW]) == "page.locator('[id=\"loginBtn\"]').fill('')"

    def test_navigate_keeps_test_data_placeholder_for_injection_later(self) -> None:
        step = make_step("web_navigate", value="${BASE_URL}/login")

        assert RESOLVER.render(step, PW, RenderContext()) == "page.goto('${BASE_URL}/login')"

    def test_resolve_step_records_sequence(self) -> None:
        assert RESOLVER.resolve_step(make_step("web_click", sequence=7, object_name="x"), PW).step_sequence == 7


class TestUnsupportedKeywords:
    def _assert_no_code(self, resolution: KeywordResolution) -> None:
        assert resolution.status is ResolutionStatus.UNSUPPORTED
        assert resolution.template is None
        assert resolution.imports == []
        assert resolution.message

    def test_unknown_keyword_returns_structured_result_with_suggestions(self) -> None:
        resolution = RESOLVER.resolve("web_clik", PW)

        self._assert_no_code(resolution)
        assert resolution.reason is UnsupportedReason.UNKNOWN_KEYWORD
        assert resolution.suggestions == ["web_click"]
        assert "not in the keyword knowledge base" in resolution.message

    @pytest.mark.parametrize("keyword", ["web_dragAndDrop", "mobile_tap", "WEB_CLICK"])
    def test_unknown_keywords_are_never_mapped(self, keyword: str) -> None:
        for target in TargetFramework:
            resolution = RESOLVER.resolve(keyword, target)
            self._assert_no_code(resolution)
            assert resolution.reason is UnsupportedReason.UNKNOWN_KEYWORD

    @pytest.mark.parametrize(
        ("keyword", "application_type"),
        [("win_click", ApplicationType.WINDOWS), ("win_TE_sendKeys", ApplicationType.TERMINAL_EMULATOR)],
    )
    def test_future_adapter_keywords(self, keyword: str, application_type: ApplicationType) -> None:
        resolution = RESOLVER.resolve(keyword, SE)

        self._assert_no_code(resolution)
        assert resolution.reason is UnsupportedReason.FUTURE_ADAPTER
        assert resolution.application_type is application_type
        assert "future" in resolution.message

    def test_keyword_without_target_mapping(self) -> None:
        kb = load_knowledge("keyword_kb")
        trimmed = {**kb, "keywords": {**kb["keywords"], "web_click": {**kb["keywords"]["web_click"]}}}
        trimmed["keywords"]["web_click"]["targets"] = {"playwright": kb["keywords"]["web_click"]["targets"]["playwright"]}
        resolver = KeywordResolver(KeywordRepository.from_dict(trimmed))

        assert resolver.resolve("web_click", PW).is_resolved
        resolution = resolver.resolve("web_click", SE)
        self._assert_no_code(resolution)
        assert resolution.reason is UnsupportedReason.TARGET_NOT_MAPPED

    def test_application_type_mismatch(self) -> None:
        resolution = RESOLVER.resolve_step(valid_step("web_click"), PW, ApplicationType.WINDOWS)

        self._assert_no_code(resolution)
        assert resolution.reason is UnsupportedReason.APPLICATION_TYPE_MISMATCH
        assert resolution.step_sequence == 1

    @pytest.mark.parametrize("keyword", ["web_clik", "win_click"])
    def test_render_refuses_unsupported_keywords(self, keyword: str) -> None:
        with pytest.raises(UnsupportedKeywordError) as info:
            RESOLVER.render(make_step(keyword, object_name="x"), PW, CONTEXTS[PW])

        assert info.value.resolution.status is ResolutionStatus.UNSUPPORTED
        assert info.value.resolution.step_sequence == 1

    def test_unsupported_result_serialises_for_the_api(self) -> None:
        dumped = RESOLVER.resolve("web_clik", PW).model_dump(by_alias=True, mode="json")

        assert dumped["status"] == "UNSUPPORTED"
        assert dumped["reason"] == "UNKNOWN_KEYWORD"
        assert dumped["targetFramework"] == "playwright"
        assert dumped["template"] is None
        assert dumped["suggestions"] == ["web_click"]


class TestParameterValidation:
    @pytest.mark.parametrize("keyword", [case[0] for case in RENDER_CASES])
    def test_missing_required_parameters(self, keyword: str) -> None:
        resolution = RESOLVER.resolve_step(make_step(keyword), PW)
        definition = REPOSITORY.get(keyword)

        assert definition is not None
        assert resolution.status is ResolutionStatus.INVALID
        assert resolution.template is None
        assert [i.parameter for i in resolution.issues] == list(definition.required)
        assert {i.code for i in resolution.issues} == {ParameterIssueCode.MISSING_REQUIRED}

    def _assert_rule(self, keyword: str, fields: dict[str, Any], valid: bool) -> None:
        resolution = RESOLVER.resolve_step(make_step(keyword, **fields), PW)

        if valid:
            assert resolution.is_resolved, resolution.issues
        else:
            assert resolution.status is ResolutionStatus.INVALID
            assert resolution.issues[0].code is ParameterIssueCode.RULE_VIOLATION
            with pytest.raises(UnsupportedKeywordError):
                RESOLVER.render(make_step(keyword, **fields), PW, CONTEXTS[PW])

    @pytest.mark.parametrize(
        "url", ["https://example.test", "http://localhost:8001/demo/login", "${BASE_URL}/login", "https://a.test/?q=1"]
    )
    def test_navigate_accepts_http_urls_and_placeholders(self, url: str) -> None:
        self._assert_rule("web_navigate", {"value": url}, valid=True)

    @pytest.mark.parametrize(
        "url", ["example.com", "ftp://example.test", "javascript:alert(1)", "/relative", "https://", "http://exa mple.test"]
    )
    def test_navigate_rejects_invalid_urls(self, url: str) -> None:
        self._assert_rule("web_navigate", {"value": url}, valid=False)

    @pytest.mark.parametrize("name", ["order_total", "total2", "_tmp"])
    def test_get_text_accepts_identifiers(self, name: str) -> None:
        self._assert_rule("web_getText", {"object_name": "x", "value": name}, valid=True)

    @pytest.mark.parametrize("name", ["1abc", "order-total", "class", "page", "driver", "__import__", "total value", "ñame"])
    def test_get_text_rejects_unsafe_variable_names(self, name: str) -> None:
        self._assert_rule("web_getText", {"object_name": "x", "value": name}, valid=False)

    @pytest.mark.parametrize("timeout", ["1", "5000", "300000"])
    def test_wait_accepts_timeouts_in_range(self, timeout: str) -> None:
        self._assert_rule("web_wait", {"object_name": "x", "value": timeout}, valid=True)

    @pytest.mark.parametrize("timeout", ["0", "300001", "abc", "5.5", "-1", "${TIMEOUT}", " 5000"])
    def test_wait_rejects_invalid_timeouts(self, timeout: str) -> None:
        self._assert_rule("web_wait", {"object_name": "x", "value": timeout}, valid=False)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_select_rejects_empty_option(self, value: str) -> None:
        self._assert_rule("web_select", {"object_name": "x", "value": value}, valid=False)

    def test_rule_violation_message_names_the_value(self) -> None:
        resolution = RESOLVER.resolve_step(make_step("web_navigate", value="example.com"), PW)

        assert "absolute http(s) URL" in resolution.issues[0].message
        assert "'example.com'" in resolution.issues[0].message

    def test_unused_parameters_produce_warnings(self) -> None:
        resolution = RESOLVER.resolve_step(make_step("web_click", object_name="x", value="ignored", expected="y"), PW)

        assert resolution.is_resolved
        assert resolution.warnings == [
            "Parameter 'value' is not used by web_click and will be ignored",
            "Parameter 'expected' is not used by web_click and will be ignored",
        ]


class TestRenderSafety:
    PAYLOAD = "x'); import os; os.system('echo pwned'); ('"

    @pytest.mark.parametrize("target", [PW, SE])
    def test_step_values_cannot_inject_code(self, target: TargetFramework) -> None:
        code = RESOLVER.render(make_step("web_setText", object_name="x", value=self.PAYLOAD), target, CONTEXTS[target])

        calls = [n for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Call)]
        assert not any(isinstance(c.func, ast.Attribute) and c.func.attr == "system" for c in calls)
        assert any(ast.literal_eval(arg) == self.PAYLOAD for c in calls for arg in c.args if isinstance(arg, ast.Constant))

    def test_locators_cannot_inject_code(self) -> None:
        code = RESOLVER.render(valid_step("web_click"), PW, RenderContext(locator=self.PAYLOAD))

        tree = ast.parse(code)
        assert len(tree.body) == 1
        assert ast.literal_eval(tree.body[0].value.func.value.args[0]) == self.PAYLOAD  # type: ignore[attr-defined]

    @pytest.mark.parametrize("by", ["ID); import os; (", "id", "CSS"])
    def test_rejects_unknown_selenium_by_constants(self, by: str) -> None:
        with pytest.raises(TemplateRenderError, match="not a Selenium By constant"):
            RESOLVER.render(valid_step("web_click"), SE, RenderContext(by=by, locator_value="x"))

    @pytest.mark.parametrize(("target", "context"), [(PW, RenderContext()), (SE, RenderContext(by="ID"))])
    def test_missing_locator_context_is_an_error(self, target: TargetFramework, context: RenderContext) -> None:
        with pytest.raises(TemplateRenderError, match="template needs"):
            RESOLVER.render(valid_step("web_click"), target, context)

    def test_locator_refs_are_emitted_as_names(self) -> None:
        step = valid_step("web_click")
        assert RESOLVER.render(step, PW, RenderContext(locator_ref="LoginPage.BUTTON")) == (
            "page.locator(LoginPage.BUTTON).click()"
        )
        assert RESOLVER.render(step, SE, RenderContext(by="ID", locator_value_ref="LoginPage.BUTTON")) == (
            "driver.find_element(By.ID, LoginPage.BUTTON).click()"
        )

    @pytest.mark.parametrize("ref", ["Page.X; import os", "Page.class", "1Page", "Page.X()", ""])
    def test_unsafe_refs_are_rejected(self, ref: str) -> None:
        with pytest.raises(TemplateRenderError, match="not a safe dotted name"):
            RESOLVER.render(valid_step("web_click"), PW, RenderContext(locator_ref=ref))

    def test_data_resolver_wraps_only_values_with_variables(self) -> None:
        context = RenderContext(locator="#u", data_resolver="test_data.resolve")
        with_var = make_step("web_setText", object_name="u", value="${USER}'x")
        plain = make_step("web_setText", object_name="u", value="plain")
        assert RESOLVER.render(with_var, PW, context) == "page.locator('#u').fill(test_data.resolve(\"${USER}'x\"))"
        assert RESOLVER.render(plain, PW, context) == "page.locator('#u').fill('plain')"

    def test_unsafe_data_resolver_is_rejected(self) -> None:
        context = RenderContext(locator="#u", data_resolver="__import__('os').system")
        with pytest.raises(TemplateRenderError):
            RESOLVER.render(make_step("web_setText", object_name="u", value="${USER}"), PW, context)

class TestSamples:
    @pytest.mark.parametrize("sample", ["login_test.json", "checkout_test.json"])
    @pytest.mark.parametrize("target", [PW, SE])
    def test_every_sample_step_resolves(self, sample: str, target: TargetFramework) -> None:
        samples = PROJECT_ROOT / "samples" / "icap"
        test_case = get_parser().parse(
            (samples / sample).read_text(encoding="utf-8"),
            test_data=(samples / "sample_testdata.json").read_text(encoding="utf-8"),
            object_repository=(samples / "sample_object_repository.json").read_text(encoding="utf-8"),
        )

        resolutions = RESOLVER.resolve_test_case(test_case, target)

        assert [r.step_sequence for r in resolutions] == [s.sequence for s in test_case.steps]
        assert all(r.is_resolved for r in resolutions), [r for r in resolutions if not r.is_resolved]
        assert all(r.warnings == [] for r in resolutions)


class TestResolutionModel:
    def test_resolved_requires_template(self) -> None:
        with pytest.raises(ValidationError, match="needs a template"):
            KeywordResolution(keyword="web_click", target_framework=PW, status=ResolutionStatus.RESOLVED)

    def test_unsupported_cannot_carry_code(self) -> None:
        with pytest.raises(ValidationError, match="must not carry generated code"):
            KeywordResolution(
                keyword="web_x",
                target_framework=PW,
                status=ResolutionStatus.UNSUPPORTED,
                reason=UnsupportedReason.UNKNOWN_KEYWORD,
                message="unknown",
                template="page.x()",
            )

    def test_unsupported_needs_reason_and_message(self) -> None:
        with pytest.raises(ValidationError, match="needs a reason"):
            KeywordResolution(keyword="web_x", target_framework=PW, status=ResolutionStatus.UNSUPPORTED)

    def test_invalid_needs_issues(self) -> None:
        with pytest.raises(ValidationError, match="needs parameter issues"):
            KeywordResolution(keyword="web_click", target_framework=PW, status=ResolutionStatus.INVALID)
