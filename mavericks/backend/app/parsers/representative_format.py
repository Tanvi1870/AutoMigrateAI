"""Representative ICAP Export Format for Prototype, version 1.0.

Our own documented JSON layout for the ICAP concepts named in the project plan: test cases, test steps and
keywords, test data, reusable blocks and the object repository. It is NOT an official or proprietary ICAP
export format; native ICAP exports are not supported until their schema is available.

Three document types share one header ({"format", "formatVersion", "documentType"}):
  TEST_CASE          one test case at the top level, or a suite in "testCases" with shared resources
  TEST_DATA          {"testData": {...}, "sensitiveKeys": [...]}
  OBJECT_REPOSITORY  {"objectRepository": {"<name>": {"type": "...", "value": "..."}}}
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from app.models.enums import ApplicationType, LocatorStrategy

FORMAT_LABEL = "Representative ICAP Export Format for Prototype"
FORMAT_ID = "representative-icap-export"
SUPPORTED_FORMAT_VERSIONS = ("1.0",)

# Size limits for uploaded documents: generous for real test cases, small enough that one upload cannot
# make parsing, generation, prompts or the UI unreasonably large.
MAX_ID_CHARS = 200
MAX_NAME_CHARS = 500
MAX_TEXT_CHARS = 5_000
MAX_VALUE_CHARS = 10_000
MAX_STEPS_PER_LIST = 1_000
MAX_BLOCKS = 200
MAX_OBJECTS = 1_000
MAX_VARIABLES = 1_000
MAX_TEST_CASES = 100

Id = Annotated[str, StringConstraints(min_length=1, max_length=MAX_ID_CHARS)]
OptionalId = Annotated[str, StringConstraints(max_length=MAX_ID_CHARS)]
Title = Annotated[str, StringConstraints(min_length=1, max_length=MAX_NAME_CHARS)]
Text = Annotated[str, StringConstraints(max_length=MAX_TEXT_CHARS)]
Value = Annotated[str, StringConstraints(max_length=MAX_VALUE_CHARS)]

DocumentType = Literal["TEST_CASE", "TEST_DATA", "OBJECT_REPOSITORY"]


class _RawModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", coerce_numbers_to_str=True
    )


class RawStep(_RawModel):
    sequence: StrictInt = Field(ge=1)
    keyword: OptionalId | None = None
    block_ref: OptionalId | None = None
    object_name: OptionalId | None = Field(default=None, alias="object")
    value: Value | None = None
    expected: Value | None = None
    description: Text | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if (self.keyword is None) == (self.block_ref is None):
            raise ValueError("a step needs exactly one of 'keyword' or 'blockRef'")
        if self.block_ref is not None and any(v is not None for v in (self.object_name, self.value, self.expected)):
            raise ValueError("a 'blockRef' step cannot have 'object', 'value' or 'expected'")
        return self


class RawBlock(_RawModel):
    block_id: Id
    name: Title
    description: Text | None = None
    steps: list[RawStep] = Field(min_length=1, max_length=MAX_STEPS_PER_LIST)


class RawObject(_RawModel):
    type: LocatorStrategy
    value: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT_CHARS)]
    description: Text | None = None


ObjectRepository = Annotated[dict[Id, RawObject], Field(max_length=MAX_OBJECTS)]
Variables = Annotated[dict[Id, Value], Field(max_length=MAX_VARIABLES)]
SensitiveKeys = Annotated[list[Id], Field(max_length=MAX_VARIABLES)]


class RawSharedResources(_RawModel):
    reusable_blocks: list[RawBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)
    object_repository: ObjectRepository = Field(default_factory=dict)
    test_data: Variables = Field(default_factory=dict)
    sensitive_keys: SensitiveKeys = Field(default_factory=list)


class RawTestCase(RawSharedResources):
    test_case_id: Id
    name: Title
    application_type: ApplicationType
    description: Text | None = None
    steps: list[RawStep] = Field(min_length=1, max_length=MAX_STEPS_PER_LIST)


class _RawDocument(_RawModel):
    format: str
    format_version: str
    document_type: DocumentType


class RawSingleTestCaseDocument(_RawDocument, RawTestCase):
    document_type: Literal["TEST_CASE"]


class RawTestSuiteDocument(_RawDocument, RawSharedResources):
    document_type: Literal["TEST_CASE"]
    test_cases: list[RawTestCase] = Field(min_length=1, max_length=MAX_TEST_CASES)


class RawTestDataDocument(_RawDocument):
    document_type: Literal["TEST_DATA"]
    test_data: Variables
    sensitive_keys: SensitiveKeys = Field(default_factory=list)


class RawObjectRepositoryDocument(_RawDocument):
    document_type: Literal["OBJECT_REPOSITORY"]
    object_repository: ObjectRepository
