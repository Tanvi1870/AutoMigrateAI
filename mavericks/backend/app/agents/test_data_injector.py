from typing import ClassVar

from app.models.canonical_test import CanonicalTestCase


class UnresolvedVariableError(LookupError):
    """Raised when a ${VAR} placeholder has no value in the test data."""


class TestDataInjector:
    __test__: ClassVar[bool] = False

    def find_parameters(self, test_case: CanonicalTestCase) -> list[str]:
        return test_case.referenced_variables()

    def find_unresolved(self, test_case: CanonicalTestCase) -> list[str]:
        return test_case.unresolved_variables()
