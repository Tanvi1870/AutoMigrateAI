"""Real qTest Manager adapter boundary.

No real qTest API access or credentials have been confirmed for this project, so this adapter never sends
or simulates a request: every operation raises QTestUnavailableError (HTTP 501) instead of returning
fabricated IDs. Implement the calls here once the qTest Manager API version, project and field mapping
(automation content, test-run status names) are confirmed against a real instance.
"""

from urllib.parse import urlparse

from pydantic import SecretStr

from app.models.canonical_test import CanonicalTestCase
from app.models.traceability import AutomationLink, ExecutionUpdate, QTestCaseRef, QTestPublication, QTestRunRef
from app.qtest.base_adapter import BaseQTestAdapter


class QTestUnavailableError(NotImplementedError):
    """The real qTest integration cannot be used; no call was made and nothing was recorded."""


class QTestAdapter(BaseQTestAdapter):
    is_mock = False
    display_name = "qTest"

    def __init__(self, base_url: str, api_token: SecretStr, project_id: str) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("QTEST_BASE_URL must be an https:// URL")
        if not project_id.isdigit():
            raise ValueError("QTEST_PROJECT_ID must be a numeric qTest project ID")
        self._base_url = f"https://{parsed.netloc}"
        self._api_token = api_token
        self._project_id = project_id

    def create_test_case(self, test_case: CanonicalTestCase) -> QTestCaseRef:
        raise self._unavailable("create_test_case")

    def link_automation(self, qtest_case_id: str, link: AutomationLink) -> None:
        raise self._unavailable("link_automation")

    def update_execution(self, qtest_case_id: str, update: ExecutionUpdate) -> QTestRunRef:
        raise self._unavailable("update_execution")

    def publish_result(self, qtest_case_id: str) -> QTestPublication:
        raise self._unavailable("publish_result")

    def _unavailable(self, operation: str) -> QTestUnavailableError:
        return QTestUnavailableError(
            f"Real qTest {operation} is not implemented: the qTest Manager API at {self._base_url} has not been "
            "confirmed for this project. Use QTEST_MODE=mock (Mock qTest) for the demo."
        )
