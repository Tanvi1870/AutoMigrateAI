import ast
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver, MissingLocatorError, UnsupportedLocatorStrategyError
from app.config import PROJECT_ROOT
from app.knowledge.keyword_repository import KeywordRepository
from app.knowledge.loader import load_knowledge
from app.models import (
    ApplicationType,
    Locator,
    LocatorResolution,
    LocatorSource,
    LocatorStatus,
    LocatorStrategy,
    MigrationReport,
    PlaywrightLocator,
    SeleniumLocator,
    TargetFramework,
    TestStep,
    UnresolvedLocatorReason,
)
from tests.factories import login_payload, make_test_case

RESOLVER = LocatorResolver(load_knowledge("locator_rules"))
PW, SE = TargetFramework.PLAYWRIGHT, TargetFramework.SELENIUM


def loc(strategy: str, value: str, name: str = "element", **extra: Any) -> Locator:
    return Locator(name=name, strategy=strategy, value=value, **extra)


# strategy, repository value, Playwright selector, Selenium (by, value)
CONVERSIONS = [
    ("id", "loginBtn", '[id="loginBtn"]', ("ID", "loginBtn")),
    ("css", "a.cart-link", "a.cart-link", ("CSS_SELECTOR", "a.cart-link")),
    ("xpath", "//button[@type='submit']", "xpath=//button[@type='submit']", ("XPATH", "//button[@type='submit']")),
    ("name", "country", '[name="country"]', ("NAME", "country")),
    ("data-testid", "add-to-cart", '[data-testid="add-to-cart"]', ("CSS_SELECTOR", '[data-testid="add-to-cart"]')),
]


class TestResolve:
    def test_resolves_from_object_repository(self) -> None:
        repository = {"loginButton": loc("id", "loginBtn", "loginButton")}
        resolution = RESOLVER.resolve("loginButton", repository, [4])
        assert resolution.status is LocatorStatus.RESOLVED
        assert resolution.locator == repository["loginButton"]
        assert resolution.step_sequences == [4]
        assert resolution.reason is None and resolution.warnings == []

    def test_missing_object_is_unresolved_not_invented(self) -> None:
        resolution = RESOLVER.resolve("logoutButton", {"loginButton": loc("id", "loginBtn", "loginButton")}, [7])
        assert resolution.status is LocatorStatus.UNRESOLVED
        assert resolution.locator is None
        assert resolution.reason is UnresolvedLocatorReason.MISSING_FROM_REPOSITORY
        assert "logoutButton" in (resolution.message or "")
        assert resolution.step_sequences == [7]

    def test_missing_object_suggests_but_does_not_apply_close_match(self) -> None:
        resolution = RESOLVER.resolve("loginButon", {"loginButton": loc("id", "loginBtn", "loginButton")})
        assert not resolution.is_resolved
        assert resolution.locator is None
        assert resolution.suggestions == ["loginButton"]

    def test_lookup_is_exact(self) -> None:
        resolution = RESOLVER.resolve("LOGINBUTTON", {"loginButton": loc("id", "loginBtn", "loginButton")})
        assert not resolution.is_resolved

    @pytest.mark.parametrize("strategy", ["automation-id", "class-name", "screen-position", "field-label"])
    def test_strategy_without_conversion_rule_is_unresolved(self, strategy: str) -> None:
        value = "1,1" if strategy == "screen-position" else "okButton"
        resolution = RESOLVER.resolve("okButton", {"okButton": loc(strategy, value, "okButton")})
        assert resolution.status is LocatorStatus.UNRESOLVED
        assert resolution.reason is UnresolvedLocatorReason.UNSUPPORTED_STRATEGY
        assert resolution.locator is None

    @pytest.mark.parametrize("source", [LocatorSource.AI_INFERRED, LocatorSource.AI_REPAIRED])
    def test_ai_sourced_locator_is_flagged_not_silent(self, source: LocatorSource) -> None:
        locator = loc("css", "#login", "loginButton", source=source, confidence=0.7)
        resolution = RESOLVER.resolve("loginButton", {"loginButton": locator})
        assert resolution.is_resolved
        assert len(resolution.warnings) == 1
        assert source in resolution.warnings[0] and "0.70" in resolution.warnings[0]


class TestResolveTestCase:
    def test_login_sample_resolves_every_object_in_first_use_order(self) -> None:
        resolutions = RESOLVER.resolve_test_case(make_test_case())
        assert [r.object_name for r in resolutions] == ["username", "password", "loginButton", "welcomeMessage"]
        assert all(r.is_resolved for r in resolutions)

    def test_collects_every_step_using_an_object(self) -> None:
        payload = login_payload()
        payload["steps"].append({"sequence": 6, "keyword": "web_click", "object": "loginButton"})
        resolutions = {r.object_name: r for r in RESOLVER.resolve_test_case(make_test_case(steps=payload["steps"]))}
        assert resolutions["loginButton"].step_sequences == [4, 6]

    def test_missing_object_reported_with_its_steps(self) -> None:
        payload = login_payload()
        del payload["objectRepository"]["welcomeMessage"]
        resolutions = RESOLVER.resolve_test_case(make_test_case(objectRepository=payload["objectRepository"]))
        unresolved = [r for r in resolutions if not r.is_resolved]
        assert [(r.object_name, r.step_sequences) for r in unresolved] == [("welcomeMessage", [5])]

    def test_unused_repository_entries_are_ignored(self) -> None:
        payload = login_payload()
        payload["objectRepository"]["unused"] = {"name": "unused", "strategy": "id", "value": "x"}
        resolutions = RESOLVER.resolve_test_case(make_test_case(objectRepository=payload["objectRepository"]))
        assert "unused" not in {r.object_name for r in resolutions}

    def test_icap_sample_object_repository_resolves(self) -> None:
        raw = json.loads((PROJECT_ROOT / "samples/icap/sample_object_repository.json").read_text(encoding="utf-8"))
        repository = {
            name: Locator(name=name, strategy=entry["type"], value=entry["value"])
            for name, entry in raw["objectRepository"].items()
        }
        for name in repository:
            resolution = RESOLVER.resolve(name, repository)
            assert resolution.is_resolved, resolution.message
            RESOLVER.to_playwright(RESOLVER.require(resolution))
            RESOLVER.to_selenium(RESOLVER.require(resolution))


class TestConversion:
    @pytest.mark.parametrize(("strategy", "value", "selector", "selenium"), CONVERSIONS)
    def test_playwright(self, strategy: str, value: str, selector: str, selenium: tuple[str, str]) -> None:
        assert RESOLVER.to_playwright(loc(strategy, value)) == PlaywrightLocator(selector=selector)
        assert RESOLVER.to_target(loc(strategy, value), PW) == PlaywrightLocator(selector=selector)

    @pytest.mark.parametrize(("strategy", "value", "selector", "selenium"), CONVERSIONS)
    def test_selenium(self, strategy: str, value: str, selector: str, selenium: tuple[str, str]) -> None:
        by, by_value = selenium
        assert RESOLVER.to_selenium(loc(strategy, value)) == SeleniumLocator(by=by, value=by_value)
        assert RESOLVER.to_target(loc(strategy, value), SE) == SeleniumLocator(by=by, value=by_value)

    def test_every_web_strategy_is_covered(self) -> None:
        web = {LocatorStrategy.ID, LocatorStrategy.CSS, LocatorStrategy.XPATH, LocatorStrategy.NAME}
        assert web | {LocatorStrategy.DATA_TESTID} == {LocatorStrategy(case[0]) for case in CONVERSIONS}

    def test_expressions(self) -> None:
        locator = loc("id", "loginBtn")
        assert RESOLVER.to_playwright(locator).expression == "page.locator('[id=\"loginBtn\"]')"
        assert RESOLVER.to_selenium(locator).expression == "(By.ID, 'loginBtn')"

    @pytest.mark.parametrize(
        ("strategy", "value", "selector"),
        [
            ("id", 'a"b', '[id="a\\"b"]'),
            ("name", "a\\b", '[name="a\\\\b"]'),
            ("data-testid", 'x"] , [id="y', '[data-testid="x\\"] , [id=\\"y"]'),
        ],
    )
    def test_quoted_values_are_css_escaped(self, strategy: str, value: str, selector: str) -> None:
        assert RESOLVER.to_playwright(loc(strategy, value)).selector == selector

    def test_selenium_id_and_name_values_stay_raw(self) -> None:
        assert RESOLVER.to_selenium(loc("id", 'a"b')).value == 'a"b'
        assert RESOLVER.to_selenium(loc("name", "a\\b")).value == "a\\b"

    def test_selenium_data_testid_value_is_escaped(self) -> None:
        assert RESOLVER.to_selenium(loc("data-testid", 'a"b')).value == '[data-testid="a\\"b"]'

    def test_expression_cannot_break_out_of_the_string_literal(self) -> None:
        payload = "x'); import os; ('"
        expression = RESOLVER.to_playwright(loc("css", payload)).expression
        call = ast.parse(expression, mode="eval").body
        assert isinstance(call, ast.Call) and ast.literal_eval(call.args[0]) == payload

    @pytest.mark.parametrize("target", [PW, SE])
    def test_unsupported_strategy_raises(self, target: TargetFramework) -> None:
        with pytest.raises(UnsupportedLocatorStrategyError):
            RESOLVER.to_target(loc("automation-id", "okButton"), target)


class TestRequire:
    def test_returns_locator_when_resolved(self) -> None:
        locator = loc("id", "loginBtn", "loginButton")
        assert RESOLVER.require(RESOLVER.resolve("loginButton", {"loginButton": locator})) == locator

    def test_raises_when_unresolved(self) -> None:
        resolution = RESOLVER.resolve("missing", {})
        with pytest.raises(MissingLocatorError) as excinfo:
            RESOLVER.require(resolution)
        assert excinfo.value.resolution == resolution


class TestRenderContext:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [(PW, "page.locator('[id=\"loginBtn\"]').click()"), (SE, "driver.find_element(By.ID, 'loginBtn').click()")],
    )
    def test_feeds_keyword_resolver(self, target: TargetFramework, expected: str) -> None:
        keywords = KeywordResolver(KeywordRepository.load())
        step = TestStep(sequence=1, keyword="web_click", object_name="loginButton")
        context = RESOLVER.render_context(loc("id", "loginBtn", "loginButton"), target)
        assert keywords.render(step, target, context, ApplicationType.WEB) == expected


class TestLocatorResolutionModel:
    def test_resolved_requires_locator(self) -> None:
        with pytest.raises(ValidationError):
            LocatorResolution(object_name="x", status=LocatorStatus.RESOLVED)

    def test_unresolved_must_not_carry_locator(self) -> None:
        with pytest.raises(ValidationError):
            LocatorResolution(
                object_name="x",
                status=LocatorStatus.UNRESOLVED,
                locator=loc("id", "x", "x"),
                reason=UnresolvedLocatorReason.MISSING_FROM_REPOSITORY,
                message="missing",
            )

    def test_unresolved_requires_reason_and_message(self) -> None:
        with pytest.raises(ValidationError):
            LocatorResolution(object_name="x", status=LocatorStatus.UNRESOLVED)

    def test_locator_name_must_match(self) -> None:
        with pytest.raises(ValidationError):
            LocatorResolution(object_name="x", status=LocatorStatus.RESOLVED, locator=loc("id", "y", "y"))

    def test_serialises_status_as_unresolved(self) -> None:
        dumped = RESOLVER.resolve("missing", {}).model_dump(mode="json", by_alias=True)
        assert dumped["status"] == "UNRESOLVED"
        assert dumped["reason"] == "MISSING_FROM_REPOSITORY"
        assert dumped["objectName"] == "missing"


class TestMigrationReport:
    def test_unresolved_locators_are_included(self) -> None:
        payload = login_payload()
        del payload["objectRepository"]["loginButton"]
        resolutions = RESOLVER.resolve_test_case(make_test_case(objectRepository=payload["objectRepository"]))
        report = MigrationReport(
            migration_id="MIG-001",
            test_case_id="ICAP-TC-001",
            generated_at=datetime(2026, 10, 7, tzinfo=UTC),
            locator_resolutions=resolutions,
        )
        assert [r.object_name for r in report.unresolved_locators] == ["loginButton"]
        dumped = report.model_dump(mode="json", by_alias=True)
        assert dumped["unresolvedLocators"][0]["status"] == "UNRESOLVED"
        assert dumped["unresolvedLocators"][0]["stepSequences"] == [4]
