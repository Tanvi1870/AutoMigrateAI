import os
import tempfile

# Test-only settings, applied before the app (and its cached Settings) is imported: TestClient sends
# Host: testserver, which TrustedHostMiddleware must accept in the test suite, and the app-wired client
# must never write migrations or generated projects into the repository's own data/ and generated/ folders.
os.environ["ALLOWED_HOSTS"] = '["localhost", "127.0.0.1", "testserver"]'
_SANDBOX = tempfile.mkdtemp(prefix="automigrate-tests-")
os.environ["DATA_DIR"] = os.path.join(_SANDBOX, "data")
os.environ["GENERATED_DIR"] = os.path.join(_SANDBOX, "generated")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client
