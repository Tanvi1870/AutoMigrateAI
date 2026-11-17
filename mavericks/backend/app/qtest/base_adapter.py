from abc import ABC, abstractmethod
from typing import ClassVar

from app.models.canonical_test import CanonicalTestCase
from app.models.traceability import AutomationLink, ExecutionUpdate, QTestCaseRef, QTestPublication, QTestRunRef


class QTestError(RuntimeError):
    """A qTest operation failed or referenced an unknown qTest test case."""


class BaseQTestAdapter(ABC):
    """Test-management port. Implementations must never report success for a call they did not make."""

    is_mock: ClassVar[bool]
    display_name: ClassVar[str]  # shown in the UI, e.g. "Mock qTest"

    @abstractmethod
    def create_test_case(self, test_case: CanonicalTestCase) -> QTestCaseRef:
        """Create the qTest test case for a source test, or return the existing one."""

    @abstractmethod
    def link_automation(self, qtest_case_id: str, link: AutomationLink) -> None:
        """Associate a generated automation script with the qTest test case."""

    @abstractmethod
    def update_execution(self, qtest_case_id: str, update: ExecutionUpdate) -> QTestRunRef:
        """Record one execution result, with its logs and screenshots, against the qTest test case."""

    @abstractmethod
    def publish_result(self, qtest_case_id: str) -> QTestPublication:
        """Publish the recorded results of the qTest test case."""
