"""Persistent migration store (H-07): records survive restarts and migration IDs are never reused."""

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models import TargetFramework
from app.models.enums import ExecutionStatus
from app.models.migration_record import MigrationRecord
from app.models.traceability import ExecutionUpdate
from app.qtest.mock_qtest_adapter import MockQTestAdapter
from app.services.repair_service import current_test_case
from app.storage.json_store import JsonFileMigrationStore, MigrationStoreError
from app.storage.migration_store import MigrationNotFoundError
from tests import test_api
from tests.factories import NOW, make_test_case
from tests.test_api import DemoBrowser, build_app, upload

PW = TargetFramework.PLAYWRIGHT


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "data" / "migrations", tmp_path / "generated"


def report_without_timestamp(client: TestClient, migration_id: str) -> dict[str, Any]:
    body = client.get(f"/api/migration/{migration_id}/report").json()
    body.pop("generatedAt")
    return body


class TestRestart:
    def test_a_repaired_demo_migration_survives_a_restart(self, paths: tuple[Path, Path]) -> None:
        directory, generated = paths
        store = JsonFileMigrationStore(directory, generated)
        with TestClient(build_app(generated, DemoBrowser(), store)) as client:
            migration_id = upload(client)["migrationId"]
            client.post("/api/migration/analyze", json={"migrationId": migration_id})
            repair = test_api.TestDemoMode().run_demo(client, migration_id)
            assert repair["status"] == "PASSED_AFTER_REPAIR"
            before = report_without_timestamp(client, migration_id)
            replayed_before = current_test_case(store.get(migration_id), PW)

        restarted = JsonFileMigrationStore(directory, generated)  # empty cache: everything comes from disk
        with TestClient(build_app(generated, DemoBrowser(), restarted)) as client:
            assert report_without_timestamp(client, migration_id) == before
            history = client.get(f"/api/migration/{migration_id}/repairs").json()
            assert [r["status"] for r in history] == ["PASSED_AFTER_REPAIR"]
            # The demo fault and the repair replay the same way after the restart.
            assert current_test_case(restarted.get(migration_id), PW) == replayed_before
            # Re-validation still sees the repaired locator and asks for a review of the AI-repaired value.
            validation = client.post("/api/migration/validate", json={"migrationId": migration_id}).json()
            assert validation["status"] == "WARNING"
            assert any("AI_REPAIRED" in issue["message"] for issue in validation["issues"])
            assert upload(client)["migrationId"] == "MIG-002"  # numbering continues

    def test_ids_skip_existing_records_and_project_folders(self, paths: tuple[Path, Path]) -> None:
        directory, generated = paths
        (generated / "playwright" / "MIG-007").mkdir(parents=True)  # a project left over from an older store
        (generated / "selenium" / "MIG-SAMPLELOGIN").mkdir(parents=True)  # non-numbered names are ignored
        store = JsonFileMigrationStore(directory, generated)
        assert store.next_id() == "MIG-008"
        (generated / "selenium" / "MIG-012").mkdir(parents=True)  # appears while running
        assert store.next_id() == "MIG-013"
        assert JsonFileMigrationStore(directory, generated).next_id() == "MIG-013"  # nothing was saved yet


class TestFiles:
    def _record(self, migration_id: str = "MIG-001") -> MigrationRecord:
        return MigrationRecord(migration_id=migration_id, created_at=NOW, test_case=make_test_case())

    def test_round_trip(self, paths: tuple[Path, Path]) -> None:
        directory, generated = paths
        record = self._record()
        JsonFileMigrationStore(directory, generated).save(record)
        assert JsonFileMigrationStore(directory, generated).get("MIG-001") == record
        assert not list(directory.glob("*.tmp"))

    def test_unknown_and_invalid_ids(self, paths: tuple[Path, Path]) -> None:
        store = JsonFileMigrationStore(*paths)
        with pytest.raises(MigrationNotFoundError):
            store.get("MIG-404")
        with pytest.raises(MigrationNotFoundError):
            store.get("../../etc/passwd")

    def test_corrupt_record_is_an_error_not_a_missing_migration(self, paths: tuple[Path, Path]) -> None:
        directory, generated = paths
        directory.mkdir(parents=True)
        (directory / "MIG-001.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(MigrationStoreError, match="MIG-001 could not be read"):
            JsonFileMigrationStore(directory, generated).get("MIG-001")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
    def test_records_are_private_to_the_service_user(self, paths: tuple[Path, Path]) -> None:
        directory, generated = paths
        JsonFileMigrationStore(directory, generated).save(self._record())
        assert (directory / "MIG-001.json").stat().st_mode & 0o077 == 0


class TestMockQTestRuns:
    def _update(self, offset_seconds: int) -> ExecutionUpdate:
        return ExecutionUpdate(
            migration_id="MIG-001",
            target_framework=PW,
            attempt=1,
            status=ExecutionStatus.PASS,
            started_at=NOW + timedelta(seconds=offset_seconds),
            duration_seconds=1.0,
        )

    def test_runs_from_different_sessions_are_never_merged(self) -> None:
        adapter = MockQTestAdapter()
        case = adapter.create_test_case(make_test_case())
        first = adapter.update_execution(case.qtest_case_id, self._update(0))
        again = adapter.update_execution(case.qtest_case_id, self._update(0))
        later = adapter.update_execution(case.qtest_case_id, self._update(3600))  # same ID/attempt, new run
        assert first.qtest_run_id == again.qtest_run_id != later.qtest_run_id
        assert len(adapter.repository.test_cases[case.qtest_case_id].runs) == 2
