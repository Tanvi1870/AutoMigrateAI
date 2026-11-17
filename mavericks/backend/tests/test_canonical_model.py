from typing import Any

import pytest
from pydantic import ValidationError

from app.models import (
    ApplicationType,
    CanonicalTestCase,
    Locator,
    LocatorSource,
    LocatorStrategy,
    ReusableBlock,
    TestData,
    TestStep,
)
from app.models.canonical_test import MASKED_VALUE
from tests.factories import login_payload, make_test_case


def _locator(**overrides: Any) -> Locator:
    return Locator.model_validate({"name": "loginButton", "strategy": "id", "value": "loginBtn"} | overrides)


class TestLocator:
    def test_defaults_to_object_repository_source(self) -> None:
        locator = _locator()

        assert locator.source is LocatorSource.OBJECT_REPOSITORY
        assert locator.ai_inferred is False
        assert locator.confidence is None

    @pytest.mark.parametrize(
        ("strategy", "expected"),
        [
            ("id", {ApplicationType.WEB}),
            ("name", {ApplicationType.WEB, ApplicationType.WINDOWS}),
            ("xpath", {ApplicationType.WEB, ApplicationType.WINDOWS}),
            ("automation-id", {ApplicationType.WINDOWS}),
            ("screen-position", {ApplicationType.TERMINAL_EMULATOR}),
            ("field-label", {ApplicationType.TERMINAL_EMULATOR}),
        ],
    )
    def test_application_types_follow_capabilities(self, strategy: str, expected: set[ApplicationType]) -> None:
        value = {"xpath": "//button", "screen-position": "3,10"}.get(strategy, "anything")

        assert _locator(strategy=strategy, value=value).application_types == expected

    @pytest.mark.parametrize("value", ["//button[@id='x']", "(//a)[1]", ".//span"])
    def test_accepts_valid_xpath(self, value: str) -> None:
        assert _locator(strategy="xpath", value=value).value == value

    @pytest.mark.parametrize("value", ["button", "#login"])
    def test_rejects_invalid_xpath(self, value: str) -> None:
        with pytest.raises(ValidationError, match="xpath"):
            _locator(strategy="xpath", value=value)

    def test_rejects_whitespace_in_id(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            _locator(value="login Btn")

    @pytest.mark.parametrize("value", ["1,1", "24,80"])
    def test_accepts_screen_position(self, value: str) -> None:
        assert _locator(strategy="screen-position", value=value).value == value

    @pytest.mark.parametrize("value", ["0,1", "3", "a,b", "3,10,2"])
    def test_rejects_invalid_screen_position(self, value: str) -> None:
        with pytest.raises(ValidationError, match="screen-position"):
            _locator(strategy="screen-position", value=value)

    @pytest.mark.parametrize("source", [LocatorSource.AI_INFERRED, LocatorSource.AI_REPAIRED])
    def test_ai_sourced_locator_requires_confidence(self, source: LocatorSource) -> None:
        with pytest.raises(ValidationError, match="confidence"):
            _locator(source=source)

        locator = _locator(source=source, confidence=0.7)
        assert locator.ai_inferred is True

    @pytest.mark.parametrize("source", [LocatorSource.OBJECT_REPOSITORY, LocatorSource.USER])
    def test_confidence_not_allowed_for_non_ai_sources(self, source: LocatorSource) -> None:
        with pytest.raises(ValidationError, match="confidence"):
            _locator(source=source, confidence=0.9)

    @pytest.mark.parametrize("confidence", [-0.1, 1.01])
    def test_confidence_is_bounded(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            _locator(source=LocatorSource.AI_INFERRED, confidence=confidence)

    @pytest.mark.parametrize("field", ["name", "value"])
    @pytest.mark.parametrize("bad", ["", "   ", " padded", "line\nbreak"])
    def test_rejects_blank_or_padded_text(self, field: str, bad: str) -> None:
        with pytest.raises(ValidationError):
            _locator(**{field: bad})

    def test_rejects_unknown_strategy_and_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            _locator(strategy="magic")
        with pytest.raises(ValidationError):
            _locator(type="id")


class TestTestStep:
    def test_parses_and_serialises_object_alias(self) -> None:
        step = TestStep.model_validate({"sequence": 1, "keyword": "web_click", "object": "loginButton"})

        assert step.object_name == "loginButton"
        assert step.model_dump(by_alias=True)["object"] == "loginButton"

    def test_accepts_python_field_names(self) -> None:
        assert TestStep(sequence=1, keyword="web_click", object_name="btn").object_name == "btn"

    @pytest.mark.parametrize("keyword", ["", "web click", "1web", "web-click"])
    def test_rejects_invalid_keyword(self, keyword: str) -> None:
        with pytest.raises(ValidationError):
            TestStep(sequence=1, keyword=keyword)

    @pytest.mark.parametrize("sequence", [0, -1])
    def test_rejects_non_positive_sequence(self, sequence: int) -> None:
        with pytest.raises(ValidationError):
            TestStep(sequence=sequence, keyword="web_click")

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            TestStep.model_validate({"sequence": 1, "keyword": "web_click", "objct": "x"})

    def test_allows_empty_value_for_clearing_fields(self) -> None:
        assert TestStep(sequence=1, keyword="web_setText", object_name="q", value="").value == ""

    def test_variable_references_cover_value_and_expected(self) -> None:
        step = TestStep(sequence=1, keyword="web_assertText", value="${A}-${B}", expected="Hi ${C} ${A}")

        assert step.variable_references == {"A", "B", "C"}

    @pytest.mark.parametrize("text", [None, "plain", "$A", "${1X}", "${}", "{A}"])
    def test_variable_references_ignore_non_placeholders(self, text: str | None) -> None:
        assert TestStep(sequence=1, keyword="web_navigate", value=text).variable_references == set()


class TestTestData:
    def test_accepts_flat_mapping(self) -> None:
        data = TestData.model_validate({"USERNAME": "demo"})

        assert data.variables == {"USERNAME": "demo"}
        assert data.sensitive_keys == set()

    def test_accepts_explicit_form(self) -> None:
        data = TestData.model_validate({"variables": {"PIN_CODE": "1234"}, "sensitiveKeys": ["PIN_CODE"]})

        assert data.sensitive_keys == {"PIN_CODE"}

    def test_coerces_numbers_to_strings(self) -> None:
        assert TestData.model_validate({"QTY": 5, "PRICE": 9.5}).variables == {"QTY": "5", "PRICE": "9.5"}

    @pytest.mark.parametrize("name", ["1ABC", "MY-VAR", "has space", ""])
    def test_rejects_invalid_variable_names(self, name: str) -> None:
        with pytest.raises(ValidationError):
            TestData.model_validate({name: "x"})

    def test_rejects_sensitive_key_without_variable(self) -> None:
        with pytest.raises(ValidationError, match="sensitive keys"):
            TestData(variables={"A": "1"}, sensitive_keys={"B"})

    @pytest.mark.parametrize(
        ("name", "sensitive"),
        [
            ("PASSWORD", True),
            ("db_passwd", True),
            ("API_KEY", True),
            ("AuthToken", True),
            ("CLIENT_SECRET", True),
            ("USER_PIN", True),
            ("ADMIN_PWD", True),
            ("USERNAME", False),
            ("BASE_URL", False),
            ("SPINNER_TEXT", False),
        ],
    )
    def test_detects_sensitive_names(self, name: str, sensitive: bool) -> None:
        assert TestData(variables={name: "v"}).is_sensitive(name) is sensitive

    def test_explicit_sensitive_key_is_respected(self) -> None:
        assert TestData(variables={"ACCOUNT": "123"}, sensitive_keys={"ACCOUNT"}).is_sensitive("ACCOUNT")

    def test_masked_hides_only_sensitive_values_without_mutating(self) -> None:
        data = TestData(variables={"USERNAME": "demo", "PASSWORD": "s3cret"})

        assert data.masked() == {"USERNAME": "demo", "PASSWORD": MASKED_VALUE}
        assert data.variables["PASSWORD"] == "s3cret"


class TestReusableBlock:
    def test_requires_steps(self) -> None:
        with pytest.raises(ValidationError):
            ReusableBlock(block_id="B1", name="Login", steps=[])

    def test_requires_increasing_sequences(self) -> None:
        steps = [TestStep(sequence=2, keyword="web_click"), TestStep(sequence=1, keyword="web_click")]

        with pytest.raises(ValidationError, match="strictly increasing"):
            ReusableBlock(block_id="B1", name="Login", steps=steps)


class TestCanonicalTestCase:
    def test_parses_login_test(self) -> None:
        test_case = make_test_case()

        assert test_case.application_type is ApplicationType.WEB
        assert len(test_case.steps) == 5
        assert test_case.steps[1].block_id == "BLK-LOGIN"
        assert test_case.test_data.variables["USERNAME"] == "demo_user"
        assert test_case.object_repository["welcomeMessage"].strategy is LocatorStrategy.CSS
        assert test_case.source is not None and test_case.source.system == "ICAP"

    def test_round_trips_through_python_and_json(self) -> None:
        original = make_test_case()

        assert CanonicalTestCase.model_validate(original.model_dump(by_alias=True)) == original
        assert CanonicalTestCase.model_validate_json(original.model_dump_json(by_alias=True)) == original

    def test_minimal_test_case_uses_defaults(self) -> None:
        test_case = CanonicalTestCase(
            test_case_id="TC-1",
            name="Minimal",
            application_type=ApplicationType.WEB,
            steps=[TestStep(sequence=1, keyword="web_navigate", value="https://example.test")],
        )

        assert test_case.test_data.variables == {}
        assert test_case.reusable_blocks == []
        assert test_case.object_repository == {}
        assert test_case.source is None

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda p: p.update(steps=[]), id="no-steps"),
            pytest.param(lambda p: p.pop("testCaseId"), id="missing-id"),
            pytest.param(lambda p: p.update(name="  "), id="blank-name"),
            pytest.param(lambda p: p.update(applicationType="MAINFRAME"), id="unknown-app-type"),
            pytest.param(lambda p: p.update(unexpected=True), id="extra-field"),
        ],
    )
    def test_rejects_invalid_structure(self, mutate) -> None:
        payload = login_payload()
        mutate(payload)

        with pytest.raises(ValidationError):
            CanonicalTestCase.model_validate(payload)

    @pytest.mark.parametrize("sequences", [[1, 1, 2, 3, 4], [1, 3, 2, 4, 5]])
    def test_rejects_non_increasing_sequences(self, sequences: list[int]) -> None:
        payload = login_payload()
        for step, sequence in zip(payload["steps"], sequences):
            step["sequence"] = sequence

        with pytest.raises(ValidationError, match="strictly increasing"):
            CanonicalTestCase.model_validate(payload)

    def test_rejects_duplicate_block_ids(self) -> None:
        payload = login_payload()
        payload["reusableBlocks"].append(payload["reusableBlocks"][0])

        with pytest.raises(ValidationError, match="unique"):
            CanonicalTestCase.model_validate(payload)

    def test_rejects_reference_to_undefined_block(self) -> None:
        payload = login_payload()
        payload["reusableBlocks"] = []

        with pytest.raises(ValidationError, match="undefined reusable blocks"):
            CanonicalTestCase.model_validate(payload)

    def test_rejects_repository_key_name_mismatch(self) -> None:
        payload = login_payload()
        payload["objectRepository"]["username"]["name"] = "user"

        with pytest.raises(ValidationError, match="does not match"):
            CanonicalTestCase.model_validate(payload)

    def test_rejects_locator_strategy_invalid_for_application(self) -> None:
        payload = login_payload()
        payload["objectRepository"]["username"] = {"name": "username", "strategy": "screen-position", "value": "1,1"}

        with pytest.raises(ValidationError, match="not valid for WEB"):
            CanonicalTestCase.model_validate(payload)

    def test_reports_variables(self) -> None:
        test_case = make_test_case(testData={"USERNAME": "demo"})

        assert test_case.referenced_variables() == ["BASE_URL", "PASSWORD", "USERNAME"]
        assert test_case.unresolved_variables() == ["BASE_URL", "PASSWORD"]

    def test_reports_missing_objects_once_in_step_order(self) -> None:
        payload = login_payload()
        payload["steps"].append({"sequence": 6, "keyword": "web_click", "object": "logoutButton"})
        payload["steps"].append({"sequence": 7, "keyword": "web_click", "object": "logoutButton"})
        del payload["objectRepository"]["username"]

        assert CanonicalTestCase.model_validate(payload).missing_objects() == ["username", "logoutButton"]

    def test_supports_windows_application_model(self) -> None:
        test_case = CanonicalTestCase.model_validate(
            {
                "testCaseId": "WIN-TC-1",
                "name": "Desktop login",
                "applicationType": "WINDOWS",
                "steps": [{"sequence": 1, "keyword": "win_click", "object": "okButton"}],
                "objectRepository": {"okButton": {"name": "okButton", "strategy": "automation-id", "value": "btnOK"}},
            }
        )

        assert test_case.application_type is ApplicationType.WINDOWS

    def test_supports_terminal_emulator_application_model(self) -> None:
        test_case = CanonicalTestCase.model_validate(
            {
                "testCaseId": "TE-TC-1",
                "name": "Green screen sign-on",
                "applicationType": "TERMINAL_EMULATOR",
                "steps": [
                    {"sequence": 1, "keyword": "win_TE_setText", "object": "userField", "value": "${USER}"},
                    {"sequence": 2, "keyword": "win_TE_setText", "object": "cmdLine", "value": "ENTER"},
                ],
                "testData": {"USER": "OPERATOR1"},
                "objectRepository": {
                    "userField": {"name": "userField", "strategy": "field-label", "value": "User ID:"},
                    "cmdLine": {"name": "cmdLine", "strategy": "screen-position", "value": "24,2"},
                },
            }
        )

        assert test_case.unresolved_variables() == []

    def test_rejects_web_locator_in_terminal_emulator_test(self) -> None:
        with pytest.raises(ValidationError, match="not valid for TERMINAL_EMULATOR"):
            CanonicalTestCase.model_validate(
                {
                    "testCaseId": "TE-TC-2",
                    "name": "Invalid",
                    "applicationType": "TERMINAL_EMULATOR",
                    "steps": [{"sequence": 1, "keyword": "win_TE_click", "object": "x"}],
                    "objectRepository": {"x": {"name": "x", "strategy": "css", "value": "#x"}},
                }
            )
