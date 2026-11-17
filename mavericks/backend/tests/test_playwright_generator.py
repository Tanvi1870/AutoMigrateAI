import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pyflakes.checker import Checker
from pyflakes.messages import UndefinedName

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver
from app.config import PROJECT_ROOT
from app.dependencies import get_keyword_repository, get_parser
from app.generators.base_generator import GenerationError, ProjectNames
from app.generators.playwright_generator import PlaywrightGenerator
from app.knowledge.loader import load_knowledge
from app.models import ArtifactKind, CanonicalTestCase, GeneratedArtifact
from tests.factories import login_payload, make_test_case

KEYWORDS = KeywordResolver(get_keyword_repository())
LOCATORS = LocatorResolver(load_knowledge("locator_rules"))
TEMPLATES = load_knowledge("framework_templates")
GENERATOR = PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES)

EXPECTED_PATHS = {
    "tests/__init__.py",
    "tests/login_test.py",
    "pages/__init__.py",
    "pages/login_page.py",
    "testdata/__init__.py",
    "testdata/loader.py",
    "testdata/login_data.json",
    "config/__init__.py",
    "config/settings.py",
    "config/navigation.py",
    "conftest.py",
    ".gitignore",
    "requirements.txt",
    "pytest.ini",
}


def by_path(artifacts: list[GeneratedArtifact]) -> dict[str, GeneratedArtifact]:
    return {artifact.path: artifact for artifact in artifacts}


def pyflakes_messages(source: str, filename: str = "generated.py") -> list[str]:
    checker = Checker(ast.parse(source, filename=filename), filename=filename)
    return [str(message) for message in checker.messages]


def sample_login() -> CanonicalTestCase:
    path = PROJECT_ROOT / "samples" / "icap" / "login_test.json"
    return get_parser().parse(path.read_bytes(), source_name=path.name)


class TestProjectLayout:
    def test_generates_complete_project(self) -> None:
        assert set(by_path(GENERATOR.build(make_test_case()))) == EXPECTED_PATHS

    def test_artifact_kinds(self) -> None:
        artifacts = by_path(GENERATOR.build(make_test_case()))
        assert artifacts["tests/login_test.py"].kind is ArtifactKind.TEST
        assert artifacts["pages/login_page.py"].kind is ArtifactKind.PAGE_OBJECT
        assert artifacts["testdata/login_data.json"].kind is ArtifactKind.TEST_DATA
        assert artifacts["config/settings.py"].kind is ArtifactKind.CONFIG
        assert artifacts["conftest.py"].kind is ArtifactKind.CONFTEST
        assert artifacts["pytest.ini"].kind is ArtifactKind.PYTEST_CONFIG
        assert artifacts["requirements.txt"].kind is ArtifactKind.REQUIREMENTS

    def test_every_python_file_is_valid_and_pyflakes_clean(self) -> None:
        for artifact in GENERATOR.build(sample_login()):
            if artifact.path.endswith(".py"):
                assert pyflakes_messages(artifact.content, artifact.path) == [], artifact.path

    def test_requirements_and_pytest_ini(self) -> None:
        artifacts = by_path(GENERATOR.build(make_test_case()))
        assert "pytest-playwright" in artifacts["requirements.txt"].content
        ini = artifacts["pytest.ini"].content
        assert ini.startswith("[pytest]\n")
        assert "pythonpath = ." in ini and "--browser chromium" in ini and "log_cli = true" in ini

    def test_generate_writes_files(self, tmp_path: Path) -> None:
        artifacts = GENERATOR.generate(make_test_case(), tmp_path)
        for artifact in artifacts:
            assert (tmp_path / artifact.path).read_text(encoding="utf-8") == artifact.content


class TestTestModule:
    def setup_method(self) -> None:
        self.artifact = by_path(GENERATOR.build(make_test_case()))["tests/login_test.py"]
        self.lines = self.artifact.content.splitlines()

    def test_imports_are_merged_and_grouped(self) -> None:
        source = self.artifact.content
        assert "import logging\n\nimport pytest\nfrom playwright.sync_api import Page, expect\n\n" in source
        assert "from pages.login_page import LoginPage\nfrom testdata.loader import DataSet\n" in source

    def test_test_function_uses_fixtures(self) -> None:
        assert "def test_login(page: Page, test_data: DataSet) -> None:" in self.lines

    def test_steps_use_kb_templates_page_object_and_test_data(self) -> None:
        body = self.artifact.content
        assert "    page.goto(test_data.resolve('${BASE_URL}/login'))" in body
        assert "    page.locator(LoginPage.USERNAME).fill(test_data.resolve('${USERNAME}'))" in body
        assert "    page.locator(LoginPage.LOGIN_BUTTON).click()" in body
        assert "    expect(page.locator(LoginPage.WELCOME_MESSAGE)).to_have_text('Welcome')" in body

    def test_step_line_map_points_at_markers(self) -> None:
        assert set(self.artifact.step_line_map) == {1, 2, 3, 4, 5}
        for sequence, line in self.artifact.step_line_map.items():
            assert self.lines[line - 1].strip().startswith(f"# [AM-STEP {sequence}] ")

    def test_each_step_is_logged_without_values(self) -> None:
        assert "    log.info('[AM-STEP 3] web_setText password (block BLK-LOGIN)')" in self.lines
        assert "demo_password" not in self.artifact.content

    def test_has_an_assertion(self) -> None:
        assert "expect(" in self.artifact.content

    @pytest.mark.parametrize(
        ("keyword", "fields", "line"),
        [
            ("web_getText", {"object": "welcomeMessage", "value": "greeting"}, "greeting = page.locator("),
            ("web_wait", {"object": "welcomeMessage", "value": "5000"}, 'wait_for(state="visible", timeout=5000)'),
            ("web_wait", {"object": "welcomeMessage"}, 'wait_for(state="visible", timeout=30000)'),
            ("web_select", {"object": "welcomeMessage", "value": "IN"}, ".select_option('IN')"),
        ],
    )
    def test_other_web_keywords(self, keyword: str, fields: dict[str, str], line: str) -> None:
        steps = login_payload()["steps"] + [{"sequence": 6, "keyword": keyword, **fields}]
        source = by_path(GENERATOR.build(make_test_case(steps=steps)))["tests/login_test.py"].content
        assert line in source
        assert not [m for m in Checker(ast.parse(source)).messages if isinstance(m, UndefinedName)]


class TestPageObjectAndData:
    def test_page_object_holds_converted_locators(self) -> None:
        payload = login_payload()
        payload["objectRepository"]["welcomeMessage"] = {"name": "welcomeMessage", "strategy": "xpath", "value": "//h1"}
        source = by_path(GENERATOR.build(make_test_case(objectRepository=payload["objectRepository"])))[
            "pages/login_page.py"
        ].content
        assert "class LoginPage:" in source
        assert "    LOGIN_BUTTON = '[id=\"loginBtn\"]'" in source
        assert "    WELCOME_MESSAGE = 'xpath=//h1'" in source

    def test_only_referenced_objects_become_constants(self) -> None:
        payload = login_payload()
        payload["objectRepository"]["unused"] = {"name": "unused", "strategy": "id", "value": "x"}
        source = by_path(GENERATOR.build(make_test_case(objectRepository=payload["objectRepository"])))[
            "pages/login_page.py"
        ].content
        assert "UNUSED" not in source

    def test_test_data_file(self) -> None:
        content = json.loads(by_path(GENERATOR.build(make_test_case()))["testdata/login_data.json"].content)
        assert content["variables"]["USERNAME"] == "demo_user"
        assert content["sensitiveKeys"] == ["PASSWORD"]

    def test_literal_values_are_not_wrapped(self) -> None:
        steps = login_payload()["steps"]
        steps[1]["value"] = "plain'user"
        source = by_path(GENERATOR.build(make_test_case(steps=steps)))["tests/login_test.py"].content
        assert ".fill(\"plain'user\")" in source


class TestNoGuessing:
    def test_missing_locator_stops_generation(self, tmp_path: Path) -> None:
        payload = login_payload()
        del payload["objectRepository"]["loginButton"]
        with pytest.raises(GenerationError, match="loginButton") as excinfo:
            GENERATOR.generate(make_test_case(objectRepository=payload["objectRepository"]), tmp_path)
        assert any("Steps 4" in problem for problem in excinfo.value.problems)
        assert list(tmp_path.iterdir()) == []

    def test_unsupported_keyword_stops_generation(self) -> None:
        steps = login_payload()["steps"] + [{"sequence": 6, "keyword": "web_hover", "object": "loginButton"}]
        with pytest.raises(GenerationError, match="web_hover"):
            GENERATOR.build(make_test_case(steps=steps))

    def test_undefined_variable_stops_generation(self) -> None:
        steps = login_payload()["steps"]
        steps[1]["value"] = "${MISSING}"
        with pytest.raises(GenerationError, match=r"\$\{MISSING\}"):
            GENERATOR.build(make_test_case(steps=steps))

    def test_reports_every_problem(self) -> None:
        payload = login_payload()
        del payload["objectRepository"]["username"]
        payload["steps"][4]["expected"] = "${NOPE}"
        with pytest.raises(GenerationError) as excinfo:
            GENERATOR.build(make_test_case(steps=payload["steps"], objectRepository=payload["objectRepository"]))
        assert len(excinfo.value.problems) == 2


class TestSafeOutput:
    def test_hostile_text_cannot_escape_comments_or_docstrings(self) -> None:
        payload = login_payload()
        repository = payload["objectRepository"]
        repository["user\rimport os"] = {"name": "user\rimport os", "strategy": "css", "value": "a\rb"}
        payload["steps"][1]["object"] = "user\rimport os"
        test_case = make_test_case(
            name='Login """ \\ Test',
            description='ends with quote"\nimport os\r\x00',
            steps=payload["steps"],
            objectRepository=repository,
        )
        for artifact in GENERATOR.build(test_case):
            if artifact.path.endswith(".py"):
                tree = ast.parse(artifact.content)
                imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
                assert "os" not in imported or artifact.path.startswith(("config/", "testdata/"))

    @pytest.mark.parametrize(
        ("name", "base", "test_file"),
        [
            ("Login Test", "login", "login_test.py"),
            ("CheckoutFlow", "checkout_flow", "checkout_flow_test.py"),
            ("123 start", "tc_123_start", "tc_123_start_test.py"),
            ("test_search", "search", "search_test.py"),
        ],
    )
    def test_project_names(self, name: str, base: str, test_file: str) -> None:
        names = ProjectNames.for_test_case(make_test_case(name=name), "{test_name}_test.py")
        assert (names.base, names.test_file) == (base, test_file)
        assert names.test_function.isidentifier() and names.page_class.isidentifier()


class TestDemoApp:
    def test_login_page_v1_and_v2(self, client: TestClient) -> None:
        assert 'id="loginBtn"' in client.get("/demo/login").text
        v2 = client.get("/demo/login", params={"variant": "v2"}).text
        assert 'data-testid="login"' in v2 and "loginBtn" not in v2

    def test_valid_login_redirects_to_welcome(self, client: TestClient) -> None:
        response = client.post(
            "/demo/login", data={"username": "demo_user", "password": "demo_password"}, follow_redirects=False
        )
        assert response.status_code == 303 and response.headers["location"] == "/demo/welcome"
        assert '<h1 id="welcome">Welcome</h1>' in client.get("/demo/welcome").text

    def test_invalid_login_is_rejected(self, client: TestClient) -> None:
        response = client.post("/demo/login", data={"username": "demo_user", "password": "nope"})
        assert response.status_code == 401 and 'id="error"' in response.text

    def test_unknown_variant_is_rejected(self, client: TestClient) -> None:
        assert client.get("/demo/login", params={"variant": "<script>"}).status_code == 422
