import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.execution.runner import ProcessOutput
from app.models import CanonicalTestCase
from app.models.migration_record import MigrationRecord
from app.storage.migration_store import InMemoryMigrationStore, MigrationBusyError, MigrationNotFoundError
from tests.test_api import build_app, ready_for_execution
from tests.test_repair import V1, FakeBrowser


def _record(migration_id: str) -> MigrationRecord:
    test_case = CanonicalTestCase.model_validate(
        {
            "testCaseId": "ICAP-TC-001",
            "name": "Login",
            "applicationType": "WEB",
            "steps": [{"sequence": 1, "keyword": "web_navigate", "value": "https://example.test"}],
        }
    )
    return MigrationRecord(migration_id=migration_id, created_at=datetime.now(UTC), test_case=test_case)


def test_next_id_is_sequential() -> None:
    store = InMemoryMigrationStore()

    assert [store.next_id(), store.next_id()] == ["MIG-001", "MIG-002"]


def test_save_and_get_round_trip() -> None:
    store = InMemoryMigrationStore()
    record = _record(store.next_id())

    store.save(record)

    assert store.get(record.migration_id) is record


def test_get_unknown_raises_not_found() -> None:
    with pytest.raises(MigrationNotFoundError):
        InMemoryMigrationStore().get("MIG-999")


class TestOperationLock:
    def test_second_operation_on_the_same_migration_is_refused(self) -> None:
        store = InMemoryMigrationStore()
        with store.operation("MIG-001"):
            with pytest.raises(MigrationBusyError, match="MIG-001 is busy"):
                with store.operation("MIG-001"):
                    pass
            with store.operation("MIG-002"):  # other migrations are independent
                pass
        with store.operation("MIG-001"):  # released afterwards, also after errors
            pass

    def test_lock_is_released_when_the_operation_fails(self) -> None:
        store = InMemoryMigrationStore()
        with pytest.raises(RuntimeError):
            with store.operation("MIG-001"):
                raise RuntimeError("boom")
        with store.operation("MIG-001"):
            pass

    def test_overlapping_api_calls_get_409_busy(self, tmp_path: Path) -> None:
        started, release = threading.Event(), threading.Event()
        browser = FakeBrowser(html=V1)

        def slow_browser(*args: object) -> ProcessOutput:
            started.set()
            release.wait(10)
            return browser(*args)

        with TestClient(build_app(tmp_path / "generated", slow_browser)) as client:
            migration_id = ready_for_execution(client)
            ref = {"migrationId": migration_id, "targetFramework": "playwright"}
            results: dict[str, object] = {}
            worker = threading.Thread(target=lambda: results.update(run=client.post("/api/migration/execute", json=ref)))
            worker.start()
            assert started.wait(10)
            try:
                for path in ("validate", "generate", "execute", "repair", "analyze"):
                    response = client.post(f"/api/migration/{path}", json=ref)
                    assert response.status_code == 409 and response.json()["code"] == "MIGRATION_BUSY", path
                assert client.get(f"/api/migration/{migration_id}/report").status_code == 200  # reads still work
            finally:
                release.set()
                worker.join(10)
            assert results["run"].status_code == 200  # type: ignore[attr-defined]
            assert client.post("/api/migration/validate", json=ref).status_code == 200  # free again
