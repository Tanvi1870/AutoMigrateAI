from app.models.canonical_test import CanonicalTestCase
from app.parsers.base_parser import BaseParser, Source, UnsupportedFormatError, decode_source
from app.parsers.csv_parser import CsvParser
from app.parsers.json_parser import JsonParser
from app.parsers.representative_format import FORMAT_LABEL


class IcapParser(BaseParser):
    """Entry point for ICAP sources: detects the input format and routes it to the matching parser.

    Representative JSON exports and CSV step tables are supported. Native ICAP exports
    (e.g. XML) are rejected explicitly because no documented schema is available to the prototype.
    """

    format_name = "icap"

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
        if source_name and source_name.lower().endswith(".csv"):
            return CsvParser(self._json_parser).parse_all(
                test_case, test_data=test_data, object_repository=object_repository, source_name=source_name
            )
        first_char = decode_source(test_case, "Test case document").lstrip()[:1]
        if first_char in ("{", "["):
            return self._json_parser.parse_all(
                test_case, test_data=test_data, object_repository=object_repository, source_name=source_name
            )
        if first_char == "<":
            raise UnsupportedFormatError(
                "XML ICAP exports are not supported: no documented ICAP export schema is available to the "
                f"prototype. Provide the test case in the {FORMAT_LABEL} (JSON)."
            )
        raise UnsupportedFormatError(f"Unrecognised ICAP input: expected a JSON document in the {FORMAT_LABEL}.")
