"""Test analysis: deterministic facts first, then an optional structured AI review of the test's intent."""

from typing import Any, ClassVar

from app.llm.base import TASK_KEY, LLMError, LLMProvider
from app.models.canonical_test import MASKED_VALUE, CanonicalTestCase
from app.models.migration_plan import AITestAnalysis, TestAnalysis

ANALYSIS_TASK = "test_analysis"
ANALYSIS_PROMPT = """You review automated UI test cases that are being migrated to Python test frameworks.
The user message is a JSON document with the canonical test case and facts computed from it.
Reply with one JSON object and nothing else, using exactly these keys:
  "summary": one or two sentences describing what the test does,
  "intent": the business behaviour the test verifies,
  "risks": a list of short notes on anything that could make the migrated test unreliable,
  "recommendations": a list of short, non-code suggestions for the reviewer.
Do not write code, selectors or framework calls. Code is produced by a separate deterministic generator."""
_ASSERTION_MARKERS = ("assert", "verify")


class TestAnalyzer:
    """Counts and classification are deterministic; the LLM only adds an advisory, schema-checked review."""

    __test__: ClassVar[bool] = False

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm

    def analyze(self, test_case: CanonicalTestCase) -> TestAnalysis:
        keywords = list(dict.fromkeys(step.keyword for step in test_case.steps))
        analysis = TestAnalysis(
            application_type=test_case.application_type,
            steps_detected=len(test_case.steps),
            keywords_detected=keywords,
            object_references=list(dict.fromkeys(s.object_name for s in test_case.steps if s.object_name)),
            data_parameters=test_case.referenced_variables(),
            reusable_blocks=len(test_case.reusable_blocks),
            has_assertion=any(marker in k.lower() for k in keywords for marker in _ASSERTION_MARKERS),
        )
        if self._llm is None:
            return analysis
        try:
            ai_analysis = self._llm.generate_json(ANALYSIS_PROMPT, llm_context(test_case, analysis), AITestAnalysis)
        except LLMError as exc:
            return analysis.model_copy(update={"warnings": [f"AI test analysis unavailable: {exc}"]})
        return analysis.model_copy(update={"ai_analysis": ai_analysis})


def llm_context(test_case: CanonicalTestCase, analysis: TestAnalysis) -> dict[str, Any]:
    """What the LLM may see: sensitive test data and values typed into sensitive fields are masked."""
    data = test_case.test_data
    steps = []
    for step in test_case.steps:
        value = step.value
        if value is not None and step.object_name and data.is_sensitive(step.object_name):
            value = MASKED_VALUE
        steps.append(
            {
                "sequence": step.sequence,
                "keyword": step.keyword,
                "object": step.object_name,
                "value": value,
                "expected": step.expected,
                "blockId": step.block_id,
            }
        )
    return {
        TASK_KEY: ANALYSIS_TASK,
        "testCase": {
            "testCaseId": test_case.test_case_id,
            "name": test_case.name,
            "description": test_case.description,
            "applicationType": test_case.application_type,
            "steps": steps,
            "testData": data.masked(),
            "reusableBlocks": [{"blockId": b.block_id, "name": b.name} for b in test_case.reusable_blocks],
        },
        "analysis": analysis.model_dump(
            mode="json", by_alias=True, exclude={"ai_analysis", "warnings"}
        ),
    }
