import csv
import io
from pathlib import Path

import pytest

from app.knowledge.keyword_repository import KeywordRepository
from app.parsers.base_parser import ParserError
from app.parsers.icap_parser import IcapParser
from app.parsers.json_parser import JsonParser

PARSER = IcapParser(JsonParser(KeywordRepository.load()))
SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "icap" / "login_test.csv"
HEADER = ["testCaseId", "testCaseName", "sequence", "keyword",
          "object", "locatorType", "locatorValue", "value", "expected", "description"]


def table(rows: list[list[str]], header: list[str] = HEADER) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def test_login_sample() -> None:
    case = PARSER.parse(SAMPLE.read_bytes(), source_name="login_test.csv")
    assert case.test_case_id == "ICAP-TC-001"
    assert len(case.steps) == 5
    assert case.steps[0].value == "http://localhost:8001/demo/login"
    assert case.steps[-1].expected == "Welcome"
    assert case.object_repository["loginButton"].value == "loginBtn"
    assert not case.missing_objects()
    assert not case.unresolved_variables()
    assert case.source is not None
    assert case.source.format == "representative-icap-csv/1.0"
    assert case.source.reference == "login_test.csv"


def test_bom_quoted_values_and_blank_cells() -> None:
    content = table([
        ["TC-1", "Login", "2", "web_click", "user", "id", "username", "", "", ""],
        ["TC-1", "Login", "1", "web_navigate", "", "", "", 'http://localhost/a,b', "", 'Open "login"\npage'],
    ])
    case = PARSER.parse(content, source_name="LOGIN.CSV")
    assert case.steps[0].value == "http://localhost/a,b"
    assert case.steps[0].metadata["description"] == 'Open "login"\npage'
    assert case.steps[1].value is None


@pytest.mark.parametrize(("content", "message"), [
    (b"testCaseId,sequence\nTC,1", "Missing column"),
    (b"testCaseId,testCaseId\nTC,TC", "unique"),
    (b"testCaseId,testCaseName,sequence,keyword,typo\n", "Unknown column"),
    (table([]), "at least one"),
    (table([["TC", "Login", "1", "web_click"]]), "expected 10 fields"),
    (table([["TC", "Login", "bad", "web_click", "", "", "", "", "", ""]]), "positive integer"),
    (table([["TC", "Login", "1", "", "", "", "", "", "", ""]]), "keyword is required"),
    (table([["TC", "Login", "1", "web_click", "", "id", "button", "", "", ""]]), "locator needs"),
    (table([
        ["TC", "Login", "1", "web_click", "button", "id", "first", "", "", ""],
        ["TC", "Login", "2", "web_click", "button", "id", "second", "", "", ""],
    ]), "conflicting locators"),
    (table([
        ["TC", "Login", "1", "web_navigate", "", "", "", "http://localhost", "", ""],
        ["TC2", "Login", "2", "web_navigate", "", "", "", "http://localhost", "", ""],
    ]), "same testCaseId"),
    (b'testCaseId,testCaseName,sequence,keyword\n"unclosed', "Invalid CSV"),
    (b"\xff", "UTF-8"),
])
def test_invalid_csv(content: bytes, message: str) -> None:
    with pytest.raises(ParserError, match=message):
        PARSER.parse(content, source_name="bad.csv")


def test_csv_reuses_keyword_validation() -> None:
    content = table([["TC", "Login", "1", "not_a_keyword", "", "", "", "", "", ""]])
    with pytest.raises(ParserError):
        PARSER.parse(content, source_name="bad.csv")


def test_duplicate_sequences_rejected() -> None:
    content = table([
        ["TC", "Login", "1", "web_click", "button", "id", "button", "", "", ""],
        ["TC", "Login", "1", "web_click", "button", "", "", "", "", ""],
    ])
    with pytest.raises(ParserError):
        PARSER.parse(content, source_name="bad.csv")
