import copy
from typing import Any

import pytest

from app.knowledge.keyword_definitions import KeywordParameter, RuleType
from app.knowledge.keyword_repository import KeywordRepository, KeywordStatus, KnowledgeBaseError
from app.knowledge.loader import load_knowledge
from app.models.enums import ApplicationType, TargetFramework

REPOSITORY = KeywordRepository.load()
MVP_KEYWORDS = ["web_click", "web_setText", "web_getText", "web_assertText", "web_navigate", "web_wait", "web_select"]

# keyword -> (status, required, optional, rule types)
EXPECTED_DEFINITIONS = {
    "web_navigate": (KeywordStatus.SUPPORTED, ("value",), (), {RuleType.URL}),
    "web_click": (KeywordStatus.SUPPORTED, ("object",), (), set()),
    "web_setText": (KeywordStatus.SUPPORTED, ("object", "value"), (), set()),
    "web_getText": (KeywordStatus.ASSUMED, ("object", "value"), (), {RuleType.PYTHON_IDENTIFIER}),
    "web_assertText": (KeywordStatus.SUPPORTED, ("object", "expected"), (), set()),
    "web_wait": (KeywordStatus.ASSUMED, ("object",), ("value",), {RuleType.INTEGER_RANGE}),
    "web_select": (KeywordStatus.SUPPORTED, ("object", "value"), (), {RuleType.NOT_EMPTY}),
}


def test_repository_contains_exactly_the_mvp_keywords() -> None:
    assert sorted(REPOSITORY.keywords) == sorted(MVP_KEYWORDS)
    assert REPOSITORY.version == "2.0"


@pytest.mark.parametrize("keyword", MVP_KEYWORDS)
def test_every_keyword_is_fully_defined(keyword: str) -> None:
    definition = REPOSITORY.get(keyword)
    status, required, optional, rules = EXPECTED_DEFINITIONS[keyword]

    assert definition is not None
    assert definition.description
    assert definition.application_type is ApplicationType.WEB
    assert definition.status is status
    assert definition.required == required
    assert definition.optional == optional
    assert {rule.rule for rule in definition.validation_rules} == rules
    assert set(definition.targets) == set(TargetFramework)
    assert all(spec.description for spec in [*definition.required_parameters, *definition.optional_parameters])


@pytest.mark.parametrize("keyword", ["web_getText", "web_wait"])
def test_assumed_keywords_explain_their_assumption(keyword: str) -> None:
    info = REPOSITORY.classify(keyword)

    assert info.status is KeywordStatus.ASSUMED
    assert info.note and "unconfirmed" in info.note


def test_web_wait_timeout_has_default_and_range() -> None:
    definition = REPOSITORY.get("web_wait")

    assert definition is not None
    assert definition.default_for(KeywordParameter.VALUE) == "30000"
    rule = definition.validation_rules[0]
    assert (rule.min, rule.max) == (1, 300000)


def test_classify_exposes_required_parameters() -> None:
    assert REPOSITORY.classify("web_setText").requires == ("object", "value")
    assert REPOSITORY.classify("web_assertText").requires == ("object", "expected")


@pytest.mark.parametrize(
    ("keyword", "application_type", "prefix"),
    [
        ("win_click", ApplicationType.WINDOWS, "win_"),
        ("win_setText", ApplicationType.WINDOWS, "win_"),
        ("win_TE_sendKeys", ApplicationType.TERMINAL_EMULATOR, "win_TE_"),
    ],
)
def test_windows_keywords_are_future_adapters(keyword: str, application_type: ApplicationType, prefix: str) -> None:
    info = REPOSITORY.classify(keyword)
    match = REPOSITORY.future_adapter(keyword)

    assert info.status is KeywordStatus.FUTURE_ADAPTER
    assert info.application_type is application_type
    assert info.requires == ()
    assert match is not None and match[0] == prefix
    assert REPOSITORY.get(keyword) is None


@pytest.mark.parametrize("keyword", ["web_dragAndDrop", "click", "WEB_CLICK", "", "mobile_tap"])
def test_unknown_keywords(keyword: str) -> None:
    info = REPOSITORY.classify(keyword)

    assert info.status is KeywordStatus.UNKNOWN
    assert info.application_type is None
    assert REPOSITORY.future_adapter(keyword) is None


@pytest.mark.parametrize(
    ("typo", "expected"), [("web_clik", "web_click"), ("WEB_SETTEXT", "web_setText"), ("web_asserttext", "web_assertText")]
)
def test_suggests_close_matches(typo: str, expected: str) -> None:
    assert REPOSITORY.suggest(typo)[0] == expected


def test_no_suggestion_for_unrelated_keyword() -> None:
    assert REPOSITORY.suggest("mobile_tap") == []


class TestKnowledgeBaseValidation:
    """A malformed KB must be rejected at load time so bad mappings can never reach code generation."""

    def _kb(self) -> dict[str, Any]:
        return copy.deepcopy(load_knowledge("keyword_kb"))

    def _rejects(self, kb: dict[str, Any], message: str) -> None:
        with pytest.raises(KnowledgeBaseError, match=message):
            KeywordRepository.from_dict(kb)

    def test_shipped_knowledge_base_is_valid(self) -> None:
        assert KeywordRepository.from_dict(self._kb()).keywords

    def test_unknown_placeholder(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["targets"]["playwright"]["python"] = "page.locator({selector}).click()"
        self._rejects(kb, r"unknown placeholder\(s\) \['selector'\]")

    def test_selenium_placeholder_in_playwright_template(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["targets"]["playwright"]["python"] = "page.locator({locator_value}).click()"
        self._rejects(kb, "playwright template uses unknown placeholder")

    def test_placeholder_for_undeclared_parameter(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["targets"]["playwright"]["python"] = "page.locator({locator}).fill({value})"
        self._rejects(kb, "undeclared parameter")

    def test_rule_on_undeclared_parameter(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["validationRules"] = [{"parameter": "expected", "rule": "notEmpty"}]
        self._rejects(kb, "validation rule targets undeclared parameter 'expected'")

    def test_integer_range_needs_bounds(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_wait"]["validationRules"] = [{"parameter": "value", "rule": "integerRange", "min": 10}]
        self._rejects(kb, "integerRange rules need")

    def test_bounds_only_for_integer_range(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_select"]["validationRules"][0]["min"] = 1
        self._rejects(kb, "do not take")

    def test_unknown_rule_type(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_select"]["validationRules"][0]["rule"] = "regex"
        self._rejects(kb, "rule")

    def test_assumed_keyword_needs_assumption(self) -> None:
        kb = self._kb()
        del kb["keywords"]["web_wait"]["assumption"]
        self._rejects(kb, "assumption")

    def test_assumption_only_for_assumed_keywords(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["assumption"] = "Not needed"
        self._rejects(kb, "assumption")

    def test_status_must_be_supported_or_assumed(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["status"] = "FUTURE_ADAPTER"
        self._rejects(kb, "SUPPORTED or ASSUMED")

    def test_duplicate_parameter(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_wait"]["requiredParameters"].append({"name": "value", "description": "dup"})
        self._rejects(kb, "declared twice")

    def test_required_parameter_cannot_have_default(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["requiredParameters"][0]["default"] = "x"
        self._rejects(kb, "required parameters cannot have a default")

    @pytest.mark.parametrize("line", ["import os; os.system('x')", "from x import *", "exec('x')"])
    def test_invalid_import_lines(self, line: str) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["targets"]["selenium"]["imports"] = [line]
        self._rejects(kb, "invalid import statement")

    def test_keyword_cannot_use_future_adapter_prefix(self) -> None:
        kb = self._kb()
        kb["keywords"]["win_click"] = kb["keywords"]["web_click"]
        self._rejects(kb, "collide with future-adapter prefixes")

    def test_unknown_fields_are_rejected(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["requires"] = ["object"]
        self._rejects(kb, "Extra inputs are not permitted")

    def test_keyword_needs_at_least_one_target(self) -> None:
        kb = self._kb()
        kb["keywords"]["web_click"]["targets"] = {}
        self._rejects(kb, "targets")
