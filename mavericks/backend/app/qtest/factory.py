from app.config import Settings
from app.qtest.base_adapter import BaseQTestAdapter
from app.qtest.mock_qtest_adapter import MockQTestAdapter
from app.qtest.qtest_adapter import QTestAdapter

MOCK_STORE_FILE = "mock_qtest.json"


def create_qtest_adapter(settings: Settings) -> BaseQTestAdapter:
    """Mock qTest unless QTEST_MODE=real and every real credential is explicitly configured."""
    if settings.qtest_mode == "mock":
        return MockQTestAdapter(settings.data_dir / MOCK_STORE_FILE)
    missing = [
        name
        for name, value in {
            "QTEST_BASE_URL": settings.qtest_base_url,
            "QTEST_API_TOKEN": settings.qtest_api_token,
            "QTEST_PROJECT_ID": settings.qtest_project_id,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"QTEST_MODE=real requires: {', '.join(missing)}")
    return QTestAdapter(settings.qtest_base_url, settings.qtest_api_token, settings.qtest_project_id)
