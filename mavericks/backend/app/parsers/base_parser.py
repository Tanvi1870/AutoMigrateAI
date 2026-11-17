from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from pydantic import ValidationError

from app.models.canonical_test import CanonicalTestCase

Source = str | bytes


class ParserError(ValueError):
    """Source content could not be converted; `errors` lists every problem found, not just the first."""

    def __init__(self, message: str, errors: Sequence[str] = ()) -> None:
        self.message = message
        self.errors = list(dict.fromkeys(errors)) or [message]
        super().__init__(f"{message}: {'; '.join(self.errors)}" if errors else message)


class UnsupportedFormatError(ParserError):
    """The input is not in a format the prototype can read."""


def decode_source(source: Source, label: str) -> str:
    if isinstance(source, bytes):
        try:
            text = source.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParserError(f"{label} is not valid UTF-8 (invalid byte at position {exc.start})") from None
    else:
        text = source.removeprefix("\ufeff")
    if not text.strip():
        raise ParserError(f"{label} is empty")
    return text


def format_validation_errors(exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        messages.append(f"{location}: {message}" if location else message)
    return messages


class BaseParser(ABC):
    format_name: ClassVar[str]

    @abstractmethod
    def parse_all(
        self,
        test_case: Source,
        *,
        test_data: Source | None = None,
        object_repository: Source | None = None,
        source_name: str | None = None,
    ) -> list[CanonicalTestCase]:
        """Convert a test-case document (plus optional shared test data / object repository) or raise ParserError."""

    def parse(
        self,
        test_case: Source,
        *,
        test_data: Source | None = None,
        object_repository: Source | None = None,
        source_name: str | None = None,
    ) -> CanonicalTestCase:
        test_cases = self.parse_all(
            test_case, test_data=test_data, object_repository=object_repository, source_name=source_name
        )
        if len(test_cases) != 1:
            raise ParserError(
                f"Expected exactly one test case but the document contains {len(test_cases)}; use parse_all()"
            )
        return test_cases[0]
