import json
from typing import Any

from app.llm.base import TASK_KEY, LLMError, LLMProvider


class MockLLMProvider(LLMProvider):
    """Deterministic offline provider; the default so the demo runs without network access or keys.

    It answers from the structured context with fixed rules, returning the same JSON shape a real model must.
    """

    name = "mock"

    def generate(self, prompt: str, context: dict[str, Any]) -> str:
        task = context.get(TASK_KEY)
        if task == "test_analysis":
            return json.dumps(_test_analysis(context))
        if task == "repair_proposal":
            return json.dumps(_repair_proposal(context))
        raise LLMError(f"{self.name} provider has no rule for task {task!r}")


def _repair_proposal(context: dict[str, Any]) -> dict[str, Any]:
    """Pick the highest-scoring candidate, or none when nothing on the page resembles the failed element."""
    step = context.get("step", {})
    error = context.get("error", {})
    old = context.get("failedLocator")
    candidates = sorted(context.get("candidates", []), key=lambda c: -c.get("score", 0))
    best = candidates[0] if candidates and candidates[0].get("score", 0) >= 0.3 else None
    diagnosis = (
        f"Step {step.get('sequence')} ({step.get('keyword')} {step.get('object')}) failed with "
        f"{error.get('type')} because {old} is no longer on the page."
    )
    if best is None:
        return {
            "diagnosis": diagnosis,
            "rootCause": "No element on the page resembles the one the step uses.",
            "candidateId": None,
            "confidence": 0.0,
            "explanation": "Repairing without a matching element would be a guess.",
        }
    element = f"{best.get('tag')} '{best.get('text')}'" if best.get("text") else best.get("tag")
    return {
        "diagnosis": diagnosis,
        "rootCause": f"The {element} is now identified by {best['locator']} instead of {old}.",
        "candidateId": best["id"],
        "confidence": round(min(0.5 + 0.45 * best.get("score", 0), 0.95), 2),
        "explanation": f"{best['locator']} is the {element} that best matches '{step.get('object')}'.",
    }


def _test_analysis(context: dict[str, Any]) -> dict[str, Any]:
    test_case = context.get("testCase", {})
    analysis = context.get("analysis", {})
    steps = test_case.get("steps", [])
    keywords = analysis.get("keywordsDetected", [])
    name = test_case.get("name") or test_case.get("testCaseId") or "The test"

    expected = [step["expected"] for step in steps if step.get("expected")]
    intent = test_case.get("description") or f"Runs '{name}'"
    if expected:
        intent = f"{intent.rstrip('.')}; checks for {', '.join(repr(value) for value in expected)}."

    risks, recommendations = [], []
    if not analysis.get("hasAssertion"):
        risks.append("The test has no assertion step, so the migrated test can pass without checking an outcome.")
    if analysis.get("dataParameters"):
        recommendations.append("Supply environment-specific test data through AM_DATA_<NAME> variables.")
    if analysis.get("reusableBlocks"):
        recommendations.append("Reusable blocks are expanded inline; review them once in the source asset.")

    return {
        "summary": (
            f"{name} is a {analysis.get('applicationType', 'UNKNOWN')} test with {len(steps)} steps "
            f"using {len(keywords)} keywords: {', '.join(keywords) or 'none'}."
        ),
        "intent": intent,
        "risks": risks,
        "recommendations": recommendations,
    }
