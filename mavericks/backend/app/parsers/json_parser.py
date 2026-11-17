import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.knowledge.keyword_repository import KeywordRepository, KeywordStatus
from app.models.canonical_test import (
    CanonicalTestCase,
    Locator,
    ReusableBlock,
    SourceInfo,
    TestData,
    TestStep,
)
from app.models.enums import ApplicationType
from app.parsers.base_parser import (
    BaseParser,
    ParserError,
    Source,
    UnsupportedFormatError,
    decode_source,
    format_validation_errors,
)
from app.parsers.representative_format import (
    FORMAT_ID,
    FORMAT_LABEL,
    SUPPORTED_FORMAT_VERSIONS,
    RawBlock,
    RawObject,
    RawObjectRepositoryDocument,
    RawSharedResources,
    RawSingleTestCaseDocument,
    RawStep,
    RawTestCase,
    RawTestDataDocument,
    RawTestSuiteDocument,
)

M = TypeVar("M", bound=BaseModel)

SOURCE_SYSTEM = "ICAP"
DEFAULT_MAX_EXPANDED_STEPS = 500
_TEST_CASE_LABEL = "Test case document"
_TEST_DATA_LABEL = "Test data document"
_OBJECT_REPOSITORY_LABEL = "Object repository document"
_STEP_FIELDS = {"object": "object_name", "value": "value", "expected": "expected"}
_JSON_KINDS = {list: "an array", str: "a string", int: "a number", float: "a number", bool: "a boolean"}


class JsonParser(BaseParser):
    """Parses the Representative ICAP Export Format for Prototype (JSON) into the Canonical Test Model."""

    format_name = FORMAT_ID

    def __init__(
        self, keyword_repository: KeywordRepository, max_block_depth: int = 5, max_expanded_steps: int = DEFAULT_MAX_EXPANDED_STEPS
    ) -> None:
        self._keywords = keyword_repository
        self._max_block_depth = max_block_depth
        self._max_expanded_steps = max_expanded_steps

    def parse_all(
        self,
        test_case: Source,
        *,
        test_data: Source | None = None,
        object_repository: Source | None = None,
        source_name: str | None = None,
    ) -> list[CanonicalTestCase]:
        document = _load_document(test_case, "TEST_CASE", _TEST_CASE_LABEL)
        cases: Sequence[RawTestCase]
        if "testCases" in document:
            suite = _validate(RawTestSuiteDocument, document, _TEST_CASE_LABEL)
            cases, shared = suite.test_cases, suite
        else:
            single = _validate(RawSingleTestCaseDocument, document, _TEST_CASE_LABEL)
            cases, shared = [single], RawSharedResources()

        external_data = (
            _validate(RawTestDataDocument, _load_document(test_data, "TEST_DATA", _TEST_DATA_LABEL), _TEST_DATA_LABEL)
            if test_data is not None
            else None
        )
        external_objects = (
            _validate(
                RawObjectRepositoryDocument,
                _load_document(object_repository, "OBJECT_REPOSITORY", _OBJECT_REPOSITORY_LABEL),
                _OBJECT_REPOSITORY_LABEL,
            )
            if object_repository is not None
            else None
        )

        duplicate_ids = sorted(i for i, n in Counter(c.test_case_id for c in cases).items() if n > 1)
        if duplicate_ids:
            raise ParserError("Test case IDs must be unique within a document", [f"duplicate: {duplicate_ids}"])

        source = SourceInfo(
            system=SOURCE_SYSTEM,
            format=f"{FORMAT_ID}/{document['formatVersion']}",
            reference=_safe_reference(source_name),
        )
        results: list[CanonicalTestCase] = []
        errors: list[str] = []
        for case in cases:
            try:
                results.append(self._build(case, shared, external_data, external_objects, source))
            except ParserError as exc:
                errors.extend(f"{case.test_case_id}: {error}" for error in exc.errors)
        if errors:
            raise ParserError("Test case document could not be converted", errors)
        return results

    def _build(
        self,
        case: RawTestCase,
        shared: RawSharedResources,
        external_data: RawTestDataDocument | None,
        external_objects: RawObjectRepositoryDocument | None,
        source: SourceInfo,
    ) -> CanonicalTestCase:
        errors: list[str] = []
        blocks = _merge_blocks([shared.reusable_blocks, case.reusable_blocks], errors)
        objects = _merge_objects(
            [
                ("the document", shared.object_repository),
                ("the test case", case.object_repository),
                ("the object repository file", external_objects.object_repository if external_objects else {}),
            ],
            errors,
        )
        # Precedence: test data file > test case > document.
        variables = shared.test_data | case.test_data | (external_data.test_data if external_data else {})
        sensitive = {*shared.sensitive_keys, *case.sensitive_keys, *(external_data.sensitive_keys if external_data else ())}

        expander = _StepExpander(
            self._keywords, case.application_type, blocks, self._max_block_depth, self._max_expanded_steps, errors
        )
        steps = expander.expand(case.steps)
        locators = {name: _to_locator(name, obj, errors) for name, obj in objects.items()}
        data = _to_test_data(variables, sensitive, errors)
        if errors:
            raise ParserError("conversion failed", errors)

        reusable_blocks = [
            ReusableBlock(block_id=block_id, name=blocks[block_id].name, steps=expander.expand_block(block_id))
            for block_id in expander.used_blocks
        ]
        try:
            return CanonicalTestCase(
                test_case_id=case.test_case_id,
                name=case.name,
                description=case.description,
                application_type=case.application_type,
                steps=steps,
                test_data=data,
                reusable_blocks=reusable_blocks,
                object_repository={name: locator for name, locator in locators.items() if locator is not None},
                source=source,
            )
        except ValidationError as exc:
            raise ParserError("conversion failed", format_validation_errors(exc)) from None


class _ExpansionLimitExceeded(Exception):
    pass


class _StepExpander:
    """Flattens steps and (nested) blockRefs into sequential canonical steps, collecting errors.

    Depth alone does not bound the work: N references per level expand to N**depth steps. Every expansion
    therefore has a budget of leaf steps and of block references, and stops as soon as either is spent.
    """

    def __init__(
        self,
        keywords: KeywordRepository,
        application_type: ApplicationType,
        blocks: dict[str, RawBlock],
        max_depth: int,
        max_steps: int,
        errors: list[str],
    ) -> None:
        self._keywords = keywords
        self._application_type = application_type
        self._blocks = blocks
        self._max_depth = max_depth
        self._max_steps = max_steps
        self._max_references = max_steps * max(max_depth, 1)
        self._errors = errors
        self._leaf_visits = 0
        self._reference_visits = 0
        self.used_blocks: dict[str, None] = {}  # insertion-ordered set

    def expand(self, raw_steps: list[RawStep]) -> list[TestStep]:
        return self._budgeted(lambda out: self._expand(raw_steps, "steps", "", None, (), out))

    def expand_block(self, block_id: str) -> list[TestStep]:
        block = self._blocks[block_id]
        return self._budgeted(
            lambda out: self._expand(block.steps, f"reusable block {block_id!r}", f"{block_id}#", block_id, (block_id,), out)
        )

    def _budgeted(self, run: Callable[[list[TestStep]], None]) -> list[TestStep]:
        self._leaf_visits = self._reference_visits = 0
        steps: list[TestStep] = []
        try:
            run(steps)
        except _ExpansionLimitExceeded:
            self._errors.append(
                f"the test case expands to more than {self._max_steps} steps or {self._max_references} block "
                "references (reusable blocks included); split it into smaller test cases"
            )
        return steps

    def _expand(
        self,
        raw_steps: list[RawStep],
        where: str,
        ref_prefix: str,
        block_id: str | None,
        stack: tuple[str, ...],
        out: list[TestStep],
    ) -> None:
        duplicates = sorted(seq for seq, n in Counter(s.sequence for s in raw_steps).items() if n > 1)
        if duplicates:
            self._errors.append(f"{where}: duplicate step sequence(s) {duplicates}")
        for raw in sorted(raw_steps, key=lambda s: s.sequence):
            ref = f"{ref_prefix}{raw.sequence}"
            if raw.block_ref is not None:
                self._expand_reference(raw.block_ref, ref, stack, out)
            else:
                self._add_step(raw, ref, block_id, out)

    def _expand_reference(self, block_ref: str, ref: str, stack: tuple[str, ...], out: list[TestStep]) -> None:
        self._reference_visits += 1
        if self._reference_visits > self._max_references:
            raise _ExpansionLimitExceeded
        if block_ref not in self._blocks:
            self._errors.append(f"step {ref}: blockRef {block_ref!r} is not defined")
        elif block_ref in stack:
            self._errors.append(f"step {ref}: reusable block cycle {' -> '.join((*stack, block_ref))}")
        elif len(stack) >= self._max_depth:
            self._errors.append(f"step {ref}: reusable blocks nested deeper than {self._max_depth} levels")
        else:
            self.used_blocks.setdefault(block_ref, None)
            block = self._blocks[block_ref]
            where = f"reusable block {block_ref!r}"
            self._expand(block.steps, where, f"{ref}/{block_ref}#", block_ref, (*stack, block_ref), out)

    def _add_step(self, raw: RawStep, ref: str, block_id: str | None, out: list[TestStep]) -> None:
        self._leaf_visits += 1
        if self._leaf_visits > self._max_steps:
            raise _ExpansionLimitExceeded
        keyword = raw.keyword or ""
        info = self._keywords.classify(keyword)
        if info.status is KeywordStatus.UNKNOWN:
            hints = self._keywords.suggest(keyword)
            hint = f" (did you mean {', '.join(map(repr, hints))}?)" if hints else ""
            self._errors.append(f"step {ref}: unsupported keyword {keyword!r}{hint}")
            return
        if info.application_type is not self._application_type:
            self._errors.append(
                f"step {ref}: keyword {keyword!r} belongs to {info.application_type} applications, "
                f"but the test case is {self._application_type}"
            )
            return
        missing = [name for name in info.requires if getattr(raw, _STEP_FIELDS[name]) is None]
        if missing:
            self._errors.append(f"step {ref}: keyword {keyword!r} requires {', '.join(map(repr, missing))}")
            return
        try:
            out.append(
                TestStep(
                    sequence=len(out) + 1,
                    keyword=keyword,
                    object_name=raw.object_name,
                    value=raw.value,
                    expected=raw.expected,
                    block_id=block_id,
                    source_step_ref=ref,
                    metadata={"description": raw.description} if raw.description else {},
                )
            )
        except ValidationError as exc:
            self._errors.extend(f"step {ref}: {message}" for message in format_validation_errors(exc))


def _load_document(source: Source, document_type: str, label: str) -> dict[str, Any]:
    text = decode_source(source, label)
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ParserError(f"{label} is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}") from None
    except _InvalidJson as exc:
        raise ParserError(f"{label} {exc}") from None
    except RecursionError:
        raise ParserError(f"{label} is nested too deeply") from None
    if not isinstance(data, dict):
        raise ParserError(f"{label} must be a JSON object, not {_JSON_KINDS.get(type(data), 'null')}")

    if data.get("format") != FORMAT_LABEL:
        raise UnsupportedFormatError(
            f"{label} is not in the {FORMAT_LABEL}: set \"format\" to {FORMAT_LABEL!r}. "
            "Native ICAP export formats are not supported by the prototype."
        )
    version = data.get("formatVersion")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise UnsupportedFormatError(
            f"{label} has unsupported formatVersion {version!r}; supported: {', '.join(SUPPORTED_FORMAT_VERSIONS)}"
        )
    if data.get("documentType") != document_type:
        raise ParserError(f"{label} must have documentType {document_type!r}, found {data.get('documentType')!r}")
    return data


class _InvalidJson(Exception):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJson(f"contains duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise _InvalidJson(f"contains unsupported JSON constant {name}")


def _validate(model: type[M], data: dict[str, Any], label: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ParserError(f"{label} is invalid", [f"{label}: {m}" for m in format_validation_errors(exc)]) from None


def _merge_blocks(groups: Iterable[list[RawBlock]], errors: list[str]) -> dict[str, RawBlock]:
    merged: dict[str, RawBlock] = {}
    for blocks in groups:
        for block in blocks:
            existing = merged.setdefault(block.block_id, block)
            if existing != block:
                errors.append(f"reusable block {block.block_id!r} is defined more than once with different content")
    return merged


def _merge_objects(sources: Iterable[tuple[str, dict[str, RawObject]]], errors: list[str]) -> dict[str, RawObject]:
    merged: dict[str, tuple[str, RawObject]] = {}
    for origin, objects in sources:
        for name, obj in objects.items():
            first_origin, first = merged.setdefault(name, (origin, obj))
            if (first.type, first.value) != (obj.type, obj.value):
                errors.append(f"object {name!r} has conflicting definitions in {first_origin} and {origin}")
    return {name: obj for name, (_, obj) in merged.items()}


def _to_locator(name: str, obj: RawObject, errors: list[str]) -> Locator | None:
    try:
        return Locator(name=name, strategy=obj.type, value=obj.value)
    except ValidationError as exc:
        errors.extend(f"object {name!r}: {message}" for message in format_validation_errors(exc))
        return None


def _to_test_data(variables: dict[str, str], sensitive: set[str], errors: list[str]) -> TestData:
    try:
        return TestData(variables=variables, sensitive_keys=sensitive)
    except ValidationError as exc:
        errors.extend(f"testData: {message}" for message in format_validation_errors(exc))
        return TestData()


def _safe_reference(source_name: str | None) -> str | None:
    """Keep only the file name: client paths must not leak into reports."""
    if not source_name:
        return None
    name = " ".join(PurePosixPath(source_name.replace("\\", "/")).name.split())
    return name or None
