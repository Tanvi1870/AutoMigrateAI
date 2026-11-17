import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from pyflakes.checker import Checker
from pyflakes.messages import UndefinedName

from app.config import PROJECT_ROOT
from app.dependencies import get_parser
from app.generators.base_generator import GenerationError
from app.generators.playwright_generator import PlaywrightGenerator
from app.generators.scaffold import navigation_policy
from app.generators.selenium_generator import SeleniumGenerator
from app.models import ArtifactKind, CanonicalTestCase
from tests.factories import login_payload, make_test_case
from tests.test_playwright_generator import (
    EXPECTED_PATHS,
    KEYWORDS,
    LOCATORS,
    TEMPLATES,
    by_path,
    pyflakes_messages,
    sample_login,
)

GENERATOR = SeleniumGenerator(KEYWORDS, LOCATORS, TEMPLATES)
PLAYWRIGHT = PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES)
SAMPLE_SOURCE = PROJECT_ROOT / "samples" / "icap" / "login_test.json"


def login_test_source(test_case: CanonicalTestCase | None = None) -> str:
    return by_path(GENERATOR.build(test_case or make_test_case()))["tests/login_test.py"].content


class TestProjectLayout:
    def test_generates_complete_project(self) -> None:
        assert set(by_path(GENERATOR.build(make_test_case()))) == EXPECTED_PATHS

    def test_artifact_kinds(self) -> None:
        artifacts = by_path(GENERATOR.build(make_test_case()))
        assert artifacts["tests/login_test.py"].kind is ArtifactKind.TEST
        assert artifacts["pages/login_page.py"].kind is ArtifactKind.PAGE_OBJECT
        assert artifacts["conftest.py"].kind is ArtifactKind.CONFTEST
        assert artifacts["requirements.txt"].kind is ArtifactKind.REQUIREMENTS
        assert artifacts["pytest.ini"].kind is ArtifactKind.PYTEST_CONFIG

    def test_every_python_file_is_valid_and_pyflakes_clean(self) -> None:
        for artifact in GENERATOR.build(sample_login()):
            if artifact.path.endswith(".py"):
                assert pyflakes_messages(artifact.content, artifact.path) == [], artifact.path

    def test_requirements_and_pytest_ini(self) -> None:
        artifacts = by_path(GENERATOR.build(make_test_case()))
        requirements = artifacts["requirements.txt"].content
        assert "selenium" in requirements and "playwright" not in requirements
        ini = artifacts["pytest.ini"].content
        assert ini.startswith("[pytest]\n")
        assert "pythonpath = ." in ini and "--selenium-browser chrome" in ini and "log_cli = true" in ini

    def test_conftest_provides_driver_fixture(self) -> None:
        conftest = by_path(GENERATOR.build(make_test_case()))["conftest.py"].content
        assert "def driver(" in conftest and "web_driver.quit()" in conftest and "save_screenshot" in conftest
        assert "playwright" not in conftest

    def test_generate_writes_files(self, tmp_path: Path) -> None:
        artifacts = GENERATOR.generate(make_test_case(), tmp_path)
        for artifact in artifacts:
            assert (tmp_path / artifact.path).read_text(encoding="utf-8") == artifact.content


class TestConftestBrowserOptions:
    """Runs the generated conftest's _start() with a fake WebDriver to see the real browser options."""

    @pytest.fixture
    def start(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
        GENERATOR.generate(make_test_case(), tmp_path)
        config = types.ModuleType("config")
        config.settings = types.SimpleNamespace()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "config", config)
        monkeypatch.setitem(sys.modules, "config.navigation", navigation_policy)
        spec = importlib.util.spec_from_file_location("am_generated_conftest", tmp_path / "conftest.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from selenium import webdriver

        fake = types.SimpleNamespace(
            ChromeOptions=webdriver.ChromeOptions,
            EdgeOptions=webdriver.EdgeOptions,
            FirefoxOptions=webdriver.FirefoxOptions,
            Chrome=lambda options: options,
            Edge=lambda options: options,
            Firefox=lambda options: options,
        )
        monkeypatch.setattr(module, "webdriver", fake)
        monkeypatch.delenv("AM_BROWSER_ARGS", raising=False)
        monkeypatch.delenv("AM_CHROME_BINARY", raising=False)
        return module._start

    def test_defaults_are_unchanged(self, start) -> None:  # noqa: ANN001
        options = start("chrome", headed=False)
        assert options.arguments == ["--headless=new"] and not options.binary_location
        assert start("chrome", headed=True).arguments == []

    def test_container_settings(self, start, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
        monkeypatch.setenv("AM_BROWSER_ARGS", "  --no-sandbox   --disable-dev-shm-usage ")
        monkeypatch.setenv("AM_CHROME_BINARY", "/usr/bin/chromium")
        chrome = start("chrome", headed=False)
        assert chrome.arguments == ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage"]
        assert chrome.binary_location == "/usr/bin/chromium"
        edge = start("edge", headed=False)
        assert "--no-sandbox" in edge.arguments and not edge.binary_location  # the Chrome binary is Chrome-only
        assert start("firefox", headed=False).arguments == ["-headless"]


class TestTestModule:
    def setup_method(self) -> None:
        self.artifact = by_path(GENERATOR.build(make_test_case()))["tests/login_test.py"]
        self.lines = self.artifact.content.splitlines()

    def test_imports_are_merged_and_grouped(self) -> None:
        assert (
            "import logging\n\nimport pytest\nfrom selenium.webdriver.common.by import By\n"
            "from selenium.webdriver.remote.webdriver import WebDriver\n\n"
            "from pages.login_page import LoginPage\nfrom testdata.loader import DataSet\n"
        ) in self.artifact.content

    def test_test_function_uses_fixtures(self) -> None:
        assert "def test_login(driver: WebDriver, test_data: DataSet) -> None:" in self.lines

    def test_steps_use_kb_templates_page_object_and_test_data(self) -> None:
        assert "    driver.get(test_data.resolve('${BASE_URL}/login'))" in self.lines
        assert "    driver.find_element(By.ID, LoginPage.USERNAME).clear()" in self.lines
        assert "    driver.find_element(By.ID, LoginPage.USERNAME).send_keys(test_data.resolve('${USERNAME}'))" in self.lines
        assert "    driver.find_element(By.ID, LoginPage.LOGIN_BUTTON).click()" in self.lines
        assert "    assert driver.find_element(By.CSS_SELECTOR, LoginPage.WELCOME_MESSAGE).text == 'Welcome'" in self.lines

    def test_step_line_map_points_at_markers(self) -> None:
        assert set(self.artifact.step_line_map) == {1, 2, 3, 4, 5}
        for sequence, line in self.artifact.step_line_map.items():
            assert self.lines[line - 1].strip().startswith(f"# [AM-STEP {sequence}] ")

    def test_values_are_not_inlined(self) -> None:
        assert "demo_password" not in self.artifact.content

    @pytest.mark.parametrize(
        ("keyword", "fields", "line"),
        [
            ("web_getText", {"object": "welcomeMessage", "value": "greeting"}, "greeting = driver.find_element("),
            (
                "web_wait",
                {"object": "welcomeMessage", "value": "5000"},
                "WebDriverWait(driver, 5).until(EC.visibility_of_element_located("
                "(By.CSS_SELECTOR, LoginPage.WELCOME_MESSAGE)))",
            ),
            ("web_wait", {"object": "welcomeMessage"}, "WebDriverWait(driver, 30).until("),
            ("web_wait", {"object": "welcomeMessage", "value": "1500"}, "WebDriverWait(driver, 1.5).until("),
            (
                "web_select",
                {"object": "welcomeMessage", "value": "IN"},
                "Select(driver.find_element(By.CSS_SELECTOR, LoginPage.WELCOME_MESSAGE)).select_by_value('IN')",
            ),
        ],
    )
    def test_other_web_keywords(self, keyword: str, fields: dict[str, str], line: str) -> None:
        steps = login_payload()["steps"] + [{"sequence": 6, "keyword": keyword, **fields}]
        source = login_test_source(make_test_case(steps=steps))
        assert line in source
        assert not [m for m in Checker(ast.parse(source)).messages if isinstance(m, UndefinedName)]


class TestPageObject:
    @pytest.mark.parametrize(
        ("strategy", "value", "by", "constant"),
        [
            ("id", "welcome", "ID", "'welcome'"),
            ("css", "#welcome", "CSS_SELECTOR", "'#welcome'"),
            ("xpath", "//h1", "XPATH", "'//h1'"),
            ("name", "greeting", "NAME", "'greeting'"),
            ("data-testid", "welcome", "CSS_SELECTOR", "'[data-testid=\"welcome\"]'"),
        ],
    )
    def test_locator_strategies(self, strategy: str, value: str, by: str, constant: str) -> None:
        repository = login_payload()["objectRepository"]
        repository["welcomeMessage"] = {"name": "welcomeMessage", "strategy": strategy, "value": value}
        artifacts = by_path(GENERATOR.build(make_test_case(objectRepository=repository)))
        assert f"    WELCOME_MESSAGE = {constant}" in artifacts["pages/login_page.py"].content
        assert f"driver.find_element(By.{by}, LoginPage.WELCOME_MESSAGE)" in artifacts["tests/login_test.py"].content

    def test_test_data_file(self) -> None:
        content = json.loads(by_path(GENERATOR.build(make_test_case()))["testdata/login_data.json"].content)
        assert content["variables"]["USERNAME"] == "demo_user"
        assert content["sensitiveKeys"] == ["PASSWORD"]


class TestNoGuessing:
    def test_missing_locator_stops_generation(self, tmp_path: Path) -> None:
        repository = login_payload()["objectRepository"]
        del repository["loginButton"]
        with pytest.raises(GenerationError, match="loginButton"):
            GENERATOR.generate(make_test_case(objectRepository=repository), tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_unsupported_keyword_stops_generation(self) -> None:
        steps = login_payload()["steps"] + [{"sequence": 6, "keyword": "web_hover", "object": "loginButton"}]
        with pytest.raises(GenerationError, match="web_hover"):
            GENERATOR.build(make_test_case(steps=steps))


class TestSameCanonicalModelBothTargets:
    def test_one_model_produces_playwright_and_selenium(self) -> None:
        test_case = sample_login()
        before = test_case.model_dump(mode="json")
        playwright = by_path(PLAYWRIGHT.build(test_case))
        selenium = by_path(GENERATOR.build(test_case))

        assert test_case.model_dump(mode="json") == before
        assert set(playwright) == set(selenium)
        assert "page.locator(" in playwright["tests/login_test.py"].content
        assert "driver.find_element(" in selenium["tests/login_test.py"].content
        pw_test, se_test = playwright["tests/login_test.py"], selenium["tests/login_test.py"]
        assert set(pw_test.step_line_map) == set(se_test.step_line_map) == {s.sequence for s in test_case.steps}
        assert _markers(pw_test.content) == _markers(se_test.content)
        assert _constants(playwright["pages/login_page.py"].content) == _constants(selenium["pages/login_page.py"].content)
        for shared in ("testdata/login_data.json", "testdata/loader.py", "config/settings.py"):
            assert playwright[shared].content == selenium[shared].content

    def test_source_file_is_unchanged(self, tmp_path: Path) -> None:
        digest = hashlib.sha256(SAMPLE_SOURCE.read_bytes()).hexdigest()
        test_case = get_parser().parse(SAMPLE_SOURCE.read_bytes(), source_name=SAMPLE_SOURCE.name)
        PLAYWRIGHT.generate(test_case, tmp_path / "playwright")
        GENERATOR.generate(test_case, tmp_path / "selenium")
        assert hashlib.sha256(SAMPLE_SOURCE.read_bytes()).hexdigest() == digest

    @pytest.mark.parametrize("generator", [PLAYWRIGHT, GENERATOR], ids=["playwright", "selenium"])
    def test_generated_project_is_collectable(self, generator: PlaywrightGenerator, tmp_path: Path) -> None:
        generator.generate(sample_login(), tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "<Function test_login" in result.stdout and "1 test collected" in result.stdout


def _markers(source: str) -> list[str]:
    return [line.strip() for line in source.splitlines() if line.strip().startswith("# [AM-STEP")]


def _constants(source: str) -> list[str]:
    return [line.split("=")[0].strip() for line in source.splitlines() if line.startswith("    ") and " = " in line]
