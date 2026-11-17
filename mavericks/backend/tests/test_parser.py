import json
import time
from typing import Any

import pytest

from app.knowledge.keyword_repository import KeywordRepository
from app.models import ApplicationType, LocatorStrategy
from app.parsers.base_parser import ParserError, UnsupportedFormatError
from app.parsers.icap_parser import IcapParser
from app.parsers.json_parser import JsonParser
from app.parsers.representative_format import FORMAT_LABEL

PARSER = JsonParser(KeywordRepository.load())
HEADER = {"format": FORMAT_LABEL, "formatVersion": "1.0"}


def case_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        **HEADER,
        "documentType": "TEST_CASE",
        "testCaseId": "TC-1",
        "name": "Search",
        "applicationType": "WEB",
        "steps": [
            {"sequence": 1, "keyword": "web_navigate", "value": "${BASE_URL}"},
            {"sequence": 2, "keyword": "web_click", "object": "searchButton"},
        ],
        "objectRepository": {"searchButton": {"type": "id", "value": "search"}},
        "testData": {"BASE_URL": "https://example.test"},
    }
    return doc | overrides


def data_doc(test_data: dict[str, Any], **overrides: Any) -> str:
    return json.dumps({**HEADER, "documentType": "TEST_DATA", "testData": test_data} | overrides)


def repo_doc(objects: dict[str, Any]) -> str:
    return json.dumps({**HEADER, "documentType": "OBJECT_REPOSITORY", "objectRepository": objects})


def parse(doc: dict[str, Any] | str, **kwargs: Any):
    return PARSER.parse(doc if isinstance(doc, str) else json.dumps(doc), **kwargs)


def errors_of(doc: dict[str, Any] | str, **kwargs: Any) -> str:
    with pytest.raises(ParserError) as info:
        parse(doc, **kwargs)
    return "\n".join(info.value.errors)


def step(sequence: int, keyword: str, **fields: Any) -> dict[str, Any]:
    return {"sequence": sequence, "keyword": keyword, **fields}


class TestHappyPath:
    def test_minimal_test_case(self) -> None:
        test_case = parse(case_doc(description="Find things"), source_name="search.json")

        assert test_case.test_case_id == "TC-1"
        assert test_case.description == "Find things"
        assert test_case.application_type is ApplicationType.WEB
        assert [s.keyword for s in test_case.steps] == ["web_navigate", "web_click"]
        assert test_case.object_repository["searchButton"].strategy is LocatorStrategy.ID
        assert test_case.test_data.variables == {"BASE_URL": "https://example.test"}
        assert test_case.source is not None
        assert (test_case.source.system, test_case.source.format) == ("ICAP", "representative-icap-export/1.0")
        assert test_case.source.reference == "search.json"

    def test_keeps_variable_placeholders(self) -> None:
        assert parse(case_doc()).steps[0].value == "${BASE_URL}"

    def test_accepts_bytes_with_bom(self) -> None:
        raw = b"\xef\xbb\xbf" + json.dumps(case_doc()).encode()

        assert PARSER.parse(raw).test_case_id == "TC-1"

    def test_steps_are_ordered_by_sequence_and_renumbered(self) -> None:
        test_case = parse(case_doc(steps=[step(20, "web_click", object="searchButton"), step(10, "web_navigate", value="x")]))

        assert [(s.sequence, s.keyword, s.source_step_ref) for s in test_case.steps] == [
            (1, "web_navigate", "10"),
            (2, "web_click", "20"),
        ]

    def test_step_description_goes_to_metadata(self) -> None:
        test_case = parse(case_doc(steps=[step(1, "web_navigate", value="x", description="Open home")]))

        assert test_case.steps[0].metadata == {"description": "Open home"}

    def test_numeric_values_are_coerced_to_strings(self) -> None:
        doc = case_doc(
            steps=[step(1, "web_wait", object="searchButton", value=5000)],
            testData={"QTY": 3},
        )

        test_case = parse(doc)
        assert test_case.steps[0].value == "5000"
        assert test_case.test_data.variables == {"QTY": "3"}

    def test_empty_string_value_is_allowed(self) -> None:
        assert parse(case_doc(steps=[step(1, "web_setText", object="searchButton", value="")])).steps[0].value == ""

    @pytest.mark.parametrize("name", ["C:\\Users\\me\\login_test.json", "/tmp/x/login_test.json", "login_test.json"])
    def test_source_reference_keeps_file_name_only(self, name: str) -> None:
        assert parse(case_doc(), source_name=name).source.reference == "login_test.json"

    def test_missing_objects_and_variables_are_reported_not_rejected(self) -> None:
        test_case = parse(case_doc(objectRepository={}, testData={}))

        assert test_case.missing_objects() == ["searchButton"]
        assert test_case.unresolved_variables() == ["BASE_URL"]


class TestReusableBlocks:
    def _doc(self, steps: list[dict], blocks: list[dict], **overrides: Any) -> dict[str, Any]:
        return case_doc(steps=steps, reusableBlocks=blocks, **overrides)

    def test_expands_block_inline_with_traceable_refs(self) -> None:
        doc = self._doc(
            [step(1, "web_navigate", value="x"), {"sequence": 2, "blockRef": "B1"}, step(3, "web_click", object="searchButton")],
            [{"blockId": "B1", "name": "Search", "steps": [step(1, "web_setText", object="searchButton", value="q"), step(2, "web_click", object="searchButton")]}],
        )

        test_case = parse(doc)

        assert [(s.sequence, s.keyword, s.block_id, s.source_step_ref) for s in test_case.steps] == [
            (1, "web_navigate", None, "1"),
            (2, "web_setText", "B1", "2/B1#1"),
            (3, "web_click", "B1", "2/B1#2"),
            (4, "web_click", None, "3"),
        ]
        assert [b.block_id for b in test_case.reusable_blocks] == ["B1"]
        assert [s.source_step_ref for s in test_case.reusable_blocks[0].steps] == ["B1#1", "B1#2"]

    def test_expands_nested_blocks(self) -> None:
        doc = self._doc(
            [{"sequence": 1, "blockRef": "OUTER"}],
            [
                {"blockId": "OUTER", "name": "Outer", "steps": [step(1, "web_navigate", value="x"), {"sequence": 2, "blockRef": "INNER"}]},
                {"blockId": "INNER", "name": "Inner", "steps": [step(1, "web_click", object="searchButton")]},
            ],
        )

        test_case = parse(doc)

        assert [(s.block_id, s.source_step_ref) for s in test_case.steps] == [
            ("OUTER", "1/OUTER#1"),
            ("INNER", "1/OUTER#2/INNER#1"),
        ]
        assert [b.block_id for b in test_case.reusable_blocks] == ["OUTER", "INNER"]

    def test_unused_blocks_are_not_included(self) -> None:
        doc = self._doc([step(1, "web_navigate", value="x")], [{"blockId": "B1", "name": "Unused", "steps": [step(1, "web_click", object="searchButton")]}])

        assert parse(doc).reusable_blocks == []

    def test_undefined_block_reference(self) -> None:
        assert "blockRef 'NOPE' is not defined" in errors_of(self._doc([{"sequence": 1, "blockRef": "NOPE"}], []))

    def test_cycle_is_detected(self) -> None:
        doc = self._doc(
            [{"sequence": 1, "blockRef": "A"}],
            [
                {"blockId": "A", "name": "A", "steps": [{"sequence": 1, "blockRef": "B"}]},
                {"blockId": "B", "name": "B", "steps": [{"sequence": 1, "blockRef": "A"}]},
            ],
        )

        assert "reusable block cycle A -> B -> A" in errors_of(doc)

    def test_self_reference_is_detected(self) -> None:
        doc = self._doc([{"sequence": 1, "blockRef": "A"}], [{"blockId": "A", "name": "A", "steps": [{"sequence": 1, "blockRef": "A"}]}])

        assert "cycle A -> A" in errors_of(doc)

    def test_depth_limit(self) -> None:
        blocks = [
            {"blockId": f"L{i}", "name": f"L{i}", "steps": [{"sequence": 1, "blockRef": f"L{i + 1}"}]} for i in range(6)
        ] + [{"blockId": "L6", "name": "L6", "steps": [step(1, "web_click", object="searchButton")]}]

        assert "nested deeper than 5 levels" in errors_of(self._doc([{"sequence": 1, "blockRef": "L0"}], blocks))

    @staticmethod
    def _fan_out(fan_out: int, depth: int, leaf: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        blocks = [{"blockId": f"B{depth}", "name": "leaf", "steps": [leaf]}]
        for level in range(depth - 1, 0, -1):
            refs = [{"sequence": i + 1, "blockRef": f"B{level + 1}"} for i in range(fan_out)]
            blocks.append({"blockId": f"B{level}", "name": f"B{level}", "steps": refs})
        return [{"sequence": i + 1, "blockRef": "B1"} for i in range(fan_out)], blocks

    def test_block_fan_out_bomb_is_rejected_quickly(self) -> None:
        # 40 references per level over 5 levels would expand to 40**5 (about 10**8) steps.
        steps, blocks = self._fan_out(40, 5, step(1, "web_click", object="searchButton"))

        started = time.monotonic()
        errors = errors_of(self._doc(steps, blocks))

        assert "expands to more than 500 steps" in errors
        assert time.monotonic() - started < 5

    def test_fan_out_of_invalid_steps_does_not_multiply_errors(self) -> None:
        steps, blocks = self._fan_out(40, 5, step(1, "no_such_keyword"))

        with pytest.raises(ParserError) as info:
            parse(self._doc(steps, blocks))

        assert len(info.value.errors) <= 500 + 1  # bounded by the step budget, not by 40**5 expansions
        assert any("expands to more than" in error for error in info.value.errors)

    def test_expansion_limit_is_configurable(self) -> None:
        parser = JsonParser(KeywordRepository.load(), max_expanded_steps=3)
        steps, blocks = self._fan_out(2, 2, step(1, "web_click", object="searchButton"))  # 2 * 2 = 4 steps

        with pytest.raises(ParserError, match="expands to more than 3 steps"):
            parser.parse(json.dumps(self._doc(steps, blocks)))
        assert len(JsonParser(KeywordRepository.load(), max_expanded_steps=4).parse(json.dumps(self._doc(steps, blocks))).steps) == 4

    def test_oversized_documents_are_rejected(self) -> None:
        assert "at most 10000 characters" in errors_of(case_doc(testData={"BASE_URL": "x" * 10_001}))
        too_many = [step(i + 1, "web_click", object="searchButton") for i in range(1_001)]
        assert "at most 1000 items" in errors_of(case_doc(steps=too_many))

    def test_conflicting_duplicate_block_definitions(self) -> None:
        block = {"blockId": "B1", "name": "Search", "steps": [step(1, "web_click", object="searchButton")]}
        other = {"blockId": "B1", "name": "Search", "steps": [step(1, "web_navigate", value="x")]}

        assert "defined more than once" in errors_of(self._doc([{"sequence": 1, "blockRef": "B1"}], [block, other]))

    def test_identical_duplicate_block_definitions_are_accepted(self) -> None:
        block = {"blockId": "B1", "name": "Search", "steps": [step(1, "web_click", object="searchButton")]}

        assert len(parse(self._doc([{"sequence": 1, "blockRef": "B1"}], [block, block])).steps) == 1

    def test_duplicate_sequence_inside_block(self) -> None:
        block = {"blockId": "B1", "name": "B", "steps": [step(1, "web_click", object="searchButton"), step(1, "web_click", object="searchButton")]}

        assert "reusable block 'B1': duplicate step sequence(s) [1]" in errors_of(self._doc([{"sequence": 1, "blockRef": "B1"}], [block]))


class TestKeywords:
    def test_unsupported_keyword_with_suggestion(self) -> None:
        errors = errors_of(case_doc(steps=[step(1, "web_clik", object="searchButton")]))

        assert "TC-1: step 1: unsupported keyword 'web_clik' (did you mean 'web_click'?)" in errors

    def test_all_unsupported_keywords_are_reported_together(self) -> None:
        errors = errors_of(case_doc(steps=[step(1, "web_dragAndDrop"), step(2, "mobile_tap"), step(3, "web_click", object="searchButton")]))

        assert "unsupported keyword 'web_dragAndDrop'" in errors
        assert "unsupported keyword 'mobile_tap'" in errors

    def test_unsupported_keyword_inside_block_reports_block_path(self) -> None:
        doc = case_doc(
            steps=[{"sequence": 4, "blockRef": "B1"}],
            reusableBlocks=[{"blockId": "B1", "name": "B", "steps": [step(2, "web_hover", object="searchButton")]}],
        )

        assert "step 4/B1#2: unsupported keyword 'web_hover'" in errors_of(doc)

    @pytest.mark.parametrize(
        ("keyword", "fields", "missing"),
        [
            ("web_click", {}, "'object'"),
            ("web_setText", {"object": "searchButton"}, "'value'"),
            ("web_assertText", {"object": "searchButton"}, "'expected'"),
            ("web_navigate", {}, "'value'"),
            ("web_select", {}, "'object', 'value'"),
        ],
    )
    def test_required_fields(self, keyword: str, fields: dict[str, Any], missing: str) -> None:
        assert f"keyword '{keyword}' requires {missing}" in errors_of(case_doc(steps=[step(1, keyword, **fields)]))

    def test_windows_keyword_in_web_test_is_rejected(self) -> None:
        errors = errors_of(case_doc(steps=[step(1, "win_click", object="searchButton")]))

        assert "keyword 'win_click' belongs to WINDOWS applications, but the test case is WEB" in errors

    def test_web_keyword_in_terminal_test_is_rejected(self) -> None:
        doc = case_doc(applicationType="TERMINAL_EMULATOR", objectRepository={}, steps=[step(1, "web_click", object="x")])

        assert "belongs to WEB applications, but the test case is TERMINAL_EMULATOR" in errors_of(doc)

    def test_future_adapter_test_cases_are_parsed_for_planning(self) -> None:
        doc = case_doc(
            applicationType="TERMINAL_EMULATOR",
            steps=[step(1, "win_TE_setText", object="userField", value="${USER}"), step(2, "win_TE_sendKeys", value="ENTER")],
            objectRepository={"userField": {"type": "field-label", "value": "User ID:"}},
            testData={"USER": "OPERATOR1"},
        )

        test_case = parse(doc)

        assert test_case.application_type is ApplicationType.TERMINAL_EMULATOR
        assert [s.keyword for s in test_case.steps] == ["win_TE_setText", "win_TE_sendKeys"]

    @pytest.mark.parametrize("keyword", ["web click", "1web_click"])
    def test_malformed_keyword_names_are_rejected(self, keyword: str) -> None:
        assert "unsupported keyword" in errors_of(case_doc(steps=[step(1, keyword, object="searchButton")]))


class TestMalformedInput:
    @pytest.mark.parametrize("raw", ["", "   ", "\ufeff"])
    def test_empty_input(self, raw: str) -> None:
        assert "Test case document is empty" in errors_of(raw)

    def test_invalid_utf8(self) -> None:
        with pytest.raises(ParserError, match="not valid UTF-8"):
            PARSER.parse(b'{"name": "\xff"}')

    def test_invalid_json_reports_position(self) -> None:
        assert "is not valid JSON: Expecting ',' delimiter at line 2, column 3" in errors_of('{"a": 1\n  "b": 2}')

    @pytest.mark.parametrize(("raw", "kind"), [("[]", "an array"), ('"text"', "a string"), ("42", "a number"), ("null", "null")])
    def test_top_level_must_be_object(self, raw: str, kind: str) -> None:
        assert f"must be a JSON object, not {kind}" in errors_of(raw)

    def test_duplicate_keys_are_rejected(self) -> None:
        raw = json.dumps(case_doc())[:-1] + ', "name": "Again"}'

        assert "duplicate JSON key 'name'" in errors_of(raw)

    @pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
    def test_non_standard_constants_are_rejected(self, constant: str) -> None:
        raw = json.dumps(case_doc())[:-1] + f', "extra": {constant}}}'

        assert f"unsupported JSON constant {constant}" in errors_of(raw)

    @pytest.mark.parametrize("format_value", [None, "ICAP", "representative icap export format for prototype"])
    def test_requires_format_label(self, format_value: str | None) -> None:
        doc = case_doc(format=format_value)

        with pytest.raises(UnsupportedFormatError, match="Native ICAP export formats are not supported"):
            parse(doc)

    def test_rejects_unsupported_version(self) -> None:
        with pytest.raises(UnsupportedFormatError, match="unsupported formatVersion '2.0'"):
            parse(case_doc(formatVersion="2.0"))

    def test_rejects_wrong_document_type(self) -> None:
        assert "must have documentType 'TEST_CASE', found 'TEST_DATA'" in errors_of(data_doc({"A": "1"}))

    @pytest.mark.parametrize(
        ("mutate", "expected"),
        [
            (lambda d: d.pop("testCaseId"), "testCaseId: Field required"),
            (lambda d: d.pop("steps"), "steps: Field required"),
            (lambda d: d.update(steps=[]), "steps: List should have at least 1 item"),
            (lambda d: d.update(applicationType="MAINFRAME"), "applicationType: Input should be 'WEB'"),
            (lambda d: d.update(objectRepository={"x": {"type": "magic", "value": "y"}}), "objectRepository.x.type"),
            (lambda d: d.update(objectRepository={"x": {"type": "id"}}), "objectRepository.x.value: Field required"),
            (lambda d: d.update(unexpected=1), "unexpected: Extra inputs are not permitted"),
            (lambda d: d["steps"][0].update(sequence="1"), "steps.0.sequence: Input should be a valid integer"),
            (lambda d: d["steps"][0].update(sequence=0), "steps.0.sequence: Input should be greater than or equal to 1"),
            (lambda d: d["steps"][1].update(objct="typo"), "steps.1.objct: Extra inputs are not permitted"),
            (lambda d: d["steps"][0].pop("keyword"), "a step needs exactly one of 'keyword' or 'blockRef'"),
            (lambda d: d["steps"][0].update(blockRef="B1"), "a step needs exactly one of 'keyword' or 'blockRef'"),
            (lambda d: d.update(testData={"A": ["list"]}), "testData.A: Input should be a valid string"),
        ],
    )
    def test_structural_errors(self, mutate, expected: str) -> None:
        doc = case_doc()
        mutate(doc)

        assert expected in errors_of(doc)

    def test_block_ref_step_cannot_carry_object(self) -> None:
        doc = case_doc(steps=[{"sequence": 1, "blockRef": "B1", "object": "searchButton"}])

        assert "a 'blockRef' step cannot have 'object', 'value' or 'expected'" in errors_of(doc)

    def test_duplicate_step_sequences(self) -> None:
        doc = case_doc(steps=[step(1, "web_navigate", value="x"), step(1, "web_click", object="searchButton")])

        assert "steps: duplicate step sequence(s) [1]" in errors_of(doc)

    def test_invalid_locator_value_names_the_object(self) -> None:
        doc = case_doc(objectRepository={"searchButton": {"type": "xpath", "value": "button"}})

        assert "object 'searchButton': xpath locator values must start with" in errors_of(doc)

    def test_locator_strategy_must_fit_application_type(self) -> None:
        doc = case_doc(objectRepository={"searchButton": {"type": "screen-position", "value": "1,1"}})

        assert "not valid for WEB applications" in errors_of(doc)

    def test_invalid_variable_name(self) -> None:
        assert "testData: variables.1ABC" in errors_of(case_doc(testData={"1ABC": "x"}))

    def test_sensitive_key_without_variable(self) -> None:
        assert "sensitive keys are not defined as variables" in errors_of(case_doc(sensitiveKeys=["SECRET_PIN"]))

    def test_parser_error_message_lists_all_errors(self) -> None:
        with pytest.raises(ParserError) as info:
            parse(case_doc(steps=[step(1, "web_foo"), step(2, "web_bar")]))

        assert len(info.value.errors) == 2
        assert "web_foo" in str(info.value) and "web_bar" in str(info.value)


class TestSharedResources:
    def test_external_object_repository_is_merged(self) -> None:
        doc = case_doc(objectRepository={})

        test_case = parse(doc, object_repository=repo_doc({"searchButton": {"type": "css", "value": "#search"}}))

        assert test_case.object_repository["searchButton"].value == "#search"

    def test_identical_object_definitions_are_accepted(self) -> None:
        test_case = parse(case_doc(), object_repository=repo_doc({"searchButton": {"type": "id", "value": "search"}}))

        assert test_case.missing_objects() == []

    def test_conflicting_object_definitions_are_rejected(self) -> None:
        errors = errors_of(case_doc(), object_repository=repo_doc({"searchButton": {"type": "id", "value": "other"}}))

        assert "object 'searchButton' has conflicting definitions in the test case and the object repository file" in errors

    def test_external_test_data_overrides_inline_data(self) -> None:
        test_case = parse(case_doc(), test_data=data_doc({"BASE_URL": "https://staging.test", "EXTRA": "1"}))

        assert test_case.test_data.variables == {"BASE_URL": "https://staging.test", "EXTRA": "1"}

    def test_external_sensitive_keys_are_applied(self) -> None:
        test_case = parse(case_doc(), test_data=data_doc({"ACCOUNT": "123"}, sensitiveKeys=["ACCOUNT"]))

        assert test_case.test_data.is_sensitive("ACCOUNT")

    def test_external_documents_are_validated(self) -> None:
        assert "Test data document must have documentType 'TEST_DATA'" in errors_of(case_doc(), test_data=repo_doc({}))
        assert "Object repository document is not valid JSON" in errors_of(case_doc(), object_repository="{oops")
        assert "Test data document: testData.A: Input should be a valid string" in errors_of(
            case_doc(), test_data=data_doc({"A": {"nested": 1}})
        )


class TestSuites:
    def _suite(self, *cases: dict[str, Any], **shared: Any) -> dict[str, Any]:
        return {**HEADER, "documentType": "TEST_CASE", "testCases": list(cases), **shared}

    def _case(self, case_id: str, **overrides: Any) -> dict[str, Any]:
        return {"testCaseId": case_id, "name": case_id, "applicationType": "WEB", "steps": [step(1, "web_click", object="btn")]} | overrides

    def test_parse_all_shares_document_resources(self) -> None:
        suite = self._suite(
            self._case("TC-A"),
            self._case("TC-B", testData={"ENV": "case"}),
            objectRepository={"btn": {"type": "id", "value": "btn"}},
            testData={"ENV": "document"},
        )

        cases = PARSER.parse_all(json.dumps(suite))

        assert [c.test_case_id for c in cases] == ["TC-A", "TC-B"]
        assert all("btn" in c.object_repository for c in cases)
        assert [c.test_data.variables["ENV"] for c in cases] == ["document", "case"]

    def test_parse_requires_single_test_case(self) -> None:
        with pytest.raises(ParserError, match="contains 2"):
            PARSER.parse(json.dumps(self._suite(self._case("TC-A"), self._case("TC-B"))))

    def test_duplicate_test_case_ids(self) -> None:
        with pytest.raises(ParserError, match="unique"):
            PARSER.parse_all(json.dumps(self._suite(self._case("TC-A"), self._case("TC-A"))))

    def test_errors_are_prefixed_with_test_case_id(self) -> None:
        suite = self._suite(self._case("TC-A"), self._case("TC-B", steps=[step(1, "web_nope")]))

        with pytest.raises(ParserError) as info:
            PARSER.parse_all(json.dumps(suite))
        assert info.value.errors == ["TC-B: step 1: unsupported keyword 'web_nope'"]

    def test_suite_cannot_mix_top_level_test_case_fields(self) -> None:
        suite = self._suite(self._case("TC-A"), testCaseId="TC-X")

        with pytest.raises(ParserError, match="testCaseId: Extra inputs are not permitted"):
            PARSER.parse_all(json.dumps(suite))


class TestIcapParser:
    ICAP = IcapParser(PARSER)

    def test_routes_json_to_representative_format_parser(self) -> None:
        assert self.ICAP.parse(json.dumps(case_doc())).test_case_id == "TC-1"

    def test_passes_shared_resources_through(self) -> None:
        test_case = self.ICAP.parse(
            json.dumps(case_doc(objectRepository={})),
            object_repository=repo_doc({"searchButton": {"type": "id", "value": "search"}}),
            source_name="search.json",
        )

        assert test_case.missing_objects() == []
        assert test_case.source.reference == "search.json"

    @pytest.mark.parametrize("raw", ["<?xml version='1.0'?><testCase/>", "  <icap/>"])
    def test_rejects_xml_exports_explicitly(self, raw: str) -> None:
        with pytest.raises(UnsupportedFormatError, match="XML ICAP exports are not supported"):
            self.ICAP.parse(raw)

    @pytest.mark.parametrize("raw", ["testCaseId,name\nTC-1,Login", "hello"])
    def test_rejects_unrecognised_input(self, raw: str) -> None:
        with pytest.raises(UnsupportedFormatError, match="Unrecognised ICAP input"):
            self.ICAP.parse(raw)

    def test_rejects_empty_input(self) -> None:
        with pytest.raises(ParserError, match="empty"):
            self.ICAP.parse(b"")
