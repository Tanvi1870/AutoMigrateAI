"""Builders for valid model instances; tests override only the fields they exercise."""

from datetime import UTC, datetime
from typing import Any

from app.models import (
    ApplicationType,
    ArtifactKind,
    CanonicalTestCase,
    ExecutionResult,
    ExecutionStatus,
    GeneratedArtifact,
    MigrationPlan,
    MigrationResult,
    RepairChangeType,
    RepairProposal,
    TargetFramework,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def login_payload() -> dict[str, Any]:
    """Canonical (already block-expanded) form of samples/icap/login_test.json."""
    return {
        "testCaseId": "ICAP-TC-001",
        "name": "Login Test",
        "applicationType": "WEB",
        "source": {"system": "ICAP", "format": "representative-icap-export/1.0", "reference": "login_test.json"},
        "steps": [
            {"sequence": 1, "keyword": "web_navigate", "value": "${BASE_URL}/login", "sourceStepRef": "1"},
            {
                "sequence": 2,
                "keyword": "web_setText",
                "object": "username",
                "value": "${USERNAME}",
                "blockId": "BLK-LOGIN",
                "sourceStepRef": "BLK-LOGIN#1",
            },
            {
                "sequence": 3,
                "keyword": "web_setText",
                "object": "password",
                "value": "${PASSWORD}",
                "blockId": "BLK-LOGIN",
                "sourceStepRef": "BLK-LOGIN#2",
            },
            {
                "sequence": 4,
                "keyword": "web_click",
                "object": "loginButton",
                "blockId": "BLK-LOGIN",
                "sourceStepRef": "BLK-LOGIN#3",
            },
            {"sequence": 5, "keyword": "web_assertText", "object": "welcomeMessage", "expected": "Welcome"},
        ],
        "testData": {"BASE_URL": "http://localhost:8001/demo", "USERNAME": "demo_user", "PASSWORD": "demo_password"},
        "reusableBlocks": [
            {
                "blockId": "BLK-LOGIN",
                "name": "Login",
                "steps": [
                    {"sequence": 1, "keyword": "web_setText", "object": "username", "value": "${USERNAME}"},
                    {"sequence": 2, "keyword": "web_setText", "object": "password", "value": "${PASSWORD}"},
                    {"sequence": 3, "keyword": "web_click", "object": "loginButton"},
                ],
            }
        ],
        "objectRepository": {
            "username": {"name": "username", "strategy": "id", "value": "username"},
            "password": {"name": "password", "strategy": "id", "value": "password"},
            "loginButton": {"name": "loginButton", "strategy": "id", "value": "loginBtn"},
            "welcomeMessage": {"name": "welcomeMessage", "strategy": "css", "value": "#welcome"},
        },
    }


def make_test_case(**overrides: Any) -> CanonicalTestCase:
    return CanonicalTestCase.model_validate(login_payload() | overrides)


def make_execution(
    status: ExecutionStatus = ExecutionStatus.PASS,
    target: TargetFramework = TargetFramework.PLAYWRIGHT,
    **overrides: Any,
) -> ExecutionResult:
    fields: dict[str, Any] = {"status": status, "target_framework": target, "started_at": NOW, "duration_seconds": 1.5}
    if status is not ExecutionStatus.PASS:
        fields |= {"failed_step": 4, "error_type": "TimeoutError", "error_message": "locator('#loginBtn') not found"}
    return ExecutionResult(**(fields | overrides))


def make_plan(**overrides: Any) -> MigrationPlan:
    fields: dict[str, Any] = {
        "migration_id": "MIG-001",
        "test_case_id": "ICAP-TC-001",
        "application_type": ApplicationType.WEB,
        "target_framework": TargetFramework.PLAYWRIGHT,
        "steps_detected": 5,
        "keywords_detected": 4,
        "keywords_mapped": 4,
        "locators_resolved": 4,
        "data_parameters": 3,
        "reusable_blocks": 1,
    }
    return MigrationPlan(**(fields | overrides))


def make_artifact(path: str = "tests/login_test.py", kind: ArtifactKind = ArtifactKind.TEST, **overrides: Any) -> GeneratedArtifact:
    fields: dict[str, Any] = {"path": path, "kind": kind, "content": "def test_login(page):\n    pass\n"}
    return GeneratedArtifact(**(fields | overrides))


def make_result(**overrides: Any) -> MigrationResult:
    fields: dict[str, Any] = {
        "migration_id": "MIG-001",
        "test_case_id": "ICAP-TC-001",
        "target_framework": TargetFramework.PLAYWRIGHT,
        "output_dir": "generated/playwright/MIG-001",
        "artifacts": [make_artifact(), make_artifact("pages/login_page.py", ArtifactKind.PAGE_OBJECT)],
        "generated_at": NOW,
    }
    return MigrationResult(**(fields | overrides))


def make_proposal(**overrides: Any) -> RepairProposal:
    fields: dict[str, Any] = {
        "step_sequence": 4,
        "change_type": RepairChangeType.LOCATOR,
        "diagnosis": "Element #loginBtn not found; DOM has a button with data-testid='login'.",
        "root_cause": "The login button's id was replaced by a data-testid.",
        "proposed_change": "Change the loginButton locator",
        "old_value": "[id=\"loginBtn\"]",
        "new_value": "[data-testid=\"login\"]",
        "confidence": 0.86,
        "explanation": "Same visible 'Log in' button; only the locator changed.",
    }
    return RepairProposal(**(fields | overrides))
