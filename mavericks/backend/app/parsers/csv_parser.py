"""CSV step tables normalized through the existing JSON validation pipeline."""

import csv
import io
import json

from app.models.canonical_test import CanonicalTestCase
from app.parsers.base_parser import BaseParser, ParserError, Source, decode_source
from app.parsers.json_parser import JsonParser
from app.parsers.representative_format import FORMAT_LABEL, MAX_STEPS_PER_LIST

REQUIRED_COLUMNS = {"testCaseId", "testCaseName", "sequence", "keyword"}
OPTIONAL_COLUMNS = {"object", "locatorType", "locatorValue", "value", "expected", "description"}


class CsvParser(BaseParser):
    format_name = "representative-icap-csv/1.0"

    def __init__(self, json_parser: JsonParser) -> None:
        self._json_parser = json_parser

    def parse_all(
        self,
        test_case: Source,
        *,
        test_data: Source | None = None,
        object_repository: Source | None = None,
        source_name: str | None = None,
    ) -> list[CanonicalTestCase]:
        reader = csv.reader(io.StringIO(decode_source(test_case, "CSV test case")), strict=True)
        steps: list[dict[str, str | int]] = []
        objects: dict[str, dict[str, str]] = {}
        identity: tuple[str, str] | None = None
        try:
            headers = next(reader)
            if len(headers) != len(set(headers)):
                raise ParserError("CSV headers must be unique")
            missing = REQUIRED_COLUMNS - set(headers)
            unknown = set(headers) - REQUIRED_COLUMNS - OPTIONAL_COLUMNS
            if missing or unknown:
                raise ParserError(
                    "Invalid CSV headers",
                    [*(f"Missing column: {name}" for name in sorted(missing)),
                     *(f"Unknown column: {name}" for name in sorted(unknown))],
                )
            for cells in reader:
                if not cells or all(not cell.strip() for cell in cells):
                    continue
                label = f"CSV line {reader.line_num}"
                if len(cells) != len(headers):
                    raise ParserError(f"{label}: expected {len(headers)} fields, got {len(cells)}")
                row = dict(zip(headers, cells))
                for name in REQUIRED_COLUMNS:
                    if not row[name].strip():
                        raise ParserError(f"{label}: {name} is required")
                row_identity = (row["testCaseId"], row["testCaseName"])
                if identity is not None and row_identity != identity:
                    raise ParserError(f"{label}: all rows must use the same testCaseId and testCaseName")
                identity = row_identity
                try:
                    sequence = int(row["sequence"])
                except ValueError:
                    raise ParserError(f"{label}: sequence must be a positive integer") from None
                step: dict[str, str | int] = {"sequence": sequence, "keyword": row["keyword"]}
                for name in ("object", "value", "expected", "description"):
                    if row.get(name):
                        step[name] = row[name]
                object_name = row.get("object", "")
                locator_type = row.get("locatorType", "")
                locator_value = row.get("locatorValue", "")
                if locator_type or locator_value:
                    if not (object_name and locator_type and locator_value):
                        raise ParserError(f"{label}: a locator needs object, locatorType and locatorValue")
                    locator = {"type": locator_type, "value": locator_value}
                    if object_name in objects and objects[object_name] != locator:
                        raise ParserError(f"{label}: conflicting locators for object '{object_name}'")
                    objects[object_name] = locator
                steps.append(step)
                if len(steps) > MAX_STEPS_PER_LIST:
                    raise ParserError(f"CSV exceeds {MAX_STEPS_PER_LIST} steps")
        except csv.Error as exc:
            raise ParserError(f"Invalid CSV at line {reader.line_num}: {exc}") from None
        if identity is None:
            raise ParserError("CSV must contain at least one test step")
        document = {
            "format": FORMAT_LABEL,
            "formatVersion": "1.0",
            "documentType": "TEST_CASE",
            "testCaseId": identity[0],
            "name": identity[1],
            "applicationType": "WEB",
            "steps": steps,
            "objectRepository": objects,
        }
        cases = self._json_parser.parse_all(
            json.dumps(document), test_data=test_data, object_repository=object_repository, source_name=source_name
        )
        for case in cases:
            if case.source is not None:
                case.source = case.source.model_copy(update={"format": self.format_name})
        return cases
