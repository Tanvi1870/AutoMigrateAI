"""Cross-model concerns: capabilities, source independence and MigrationRecord integrity."""

import pytest
from pydantic import BaseModel, ValidationError

import app.models as models
from app.models import (
    LOCATOR_STRATEGIES,
    SUPPORTED_TARGETS,
    ApplicationType,
    ExecutionStatus,
    LocatorStrategy,
    MigrationRecord,
    RepairResult,
    RepairStatus,
    TargetFramework,
)
from tests.factories import NOW, make_execution, make_plan, make_proposal, make_result, make_test_case


class TestCapabilities:
    def test_every_application_type_is_described(self) -> None:
        assert set(LOCATOR_STRATEGIES) == set(ApplicationType)
        assert set(SUPPORTED_TARGETS) == set(ApplicationType)

    def test_every_locator_strategy_belongs_to_an_application_type(self) -> None:
        assert set().union(*LOCATOR_STRATEGIES.values()) == set(LocatorStrategy)

    def test_web_supports_both_targets(self) -> None:
        assert SUPPORTED_TARGETS[ApplicationType.WEB] == set(TargetFramework)

    @pytest.mark.parametrize("app_type", [ApplicationType.WINDOWS, ApplicationType.TERMINAL_EMULATOR])
    def test_future_adapters_have_no_targets_yet(self, app_type: ApplicationType) -> None:
        assert SUPPORTED_TARGETS[app_type] == frozenset()

    def test_capability_maps_are_read_only(self) -> None:
        with pytest.raises(TypeError):
            SUPPORTED_TARGETS[ApplicationType.WINDOWS] = frozenset(TargetFramework)  # type: ignore[index]


def test_models_are_independent_of_icap_naming() -> None:
    model_classes = [obj for obj in vars(models).values() if isinstance(obj, type) and issubclass(obj, BaseModel)]

    for model in model_classes:
        names = set(model.model_fields) | {field.alias or "" for field in model.model_fields.values()}
        assert not any("icap" in name.lower() for name in names), model.__name__


class TestMigrationRecord:
    def _record(self, **overrides) -> MigrationRecord:
        fields = {"migration_id": "MIG-001", "created_at": NOW, "test_case": make_test_case()}
        return MigrationRecord(**(fields | overrides))

    def test_valid_record_round_trips(self) -> None:
        record = self._record(
            plans={TargetFramework.PLAYWRIGHT: make_plan()},
            results={TargetFramework.PLAYWRIGHT: make_result()},
            executions={TargetFramework.PLAYWRIGHT: [make_execution(ExecutionStatus.FAIL), make_execution(attempt=2)]},
            repairs=[
                RepairResult(
                    migration_id="MIG-001",
                    target_framework=TargetFramework.PLAYWRIGHT,
                    proposal=make_proposal(),
                    status=RepairStatus.PASSED_AFTER_REPAIR,
                    re_execution=make_execution(attempt=2),
                    created_at=NOW,
                )
            ],
        )

        assert MigrationRecord.model_validate_json(record.model_dump_json(by_alias=True)) == record

    def test_rejects_plan_stored_under_wrong_target(self) -> None:
        with pytest.raises(ValidationError, match="stored under"):
            self._record(plans={TargetFramework.SELENIUM: make_plan()})

    def test_rejects_result_for_other_migration(self) -> None:
        with pytest.raises(ValidationError, match="different migration"):
            self._record(results={TargetFramework.PLAYWRIGHT: make_result(migration_id="MIG-002")})

    def test_rejects_plan_for_other_test_case(self) -> None:
        with pytest.raises(ValidationError, match="different migration or test case"):
            self._record(plans={TargetFramework.PLAYWRIGHT: make_plan(test_case_id="ICAP-TC-999")})

    def test_rejects_execution_stored_under_wrong_target(self) -> None:
        with pytest.raises(ValidationError, match="different framework"):
            self._record(executions={TargetFramework.SELENIUM: [make_execution()]})

    def test_rejects_repair_for_other_migration(self) -> None:
        repair = RepairResult(
            migration_id="MIG-002", target_framework=TargetFramework.PLAYWRIGHT, proposal=make_proposal(), created_at=NOW
        )

        with pytest.raises(ValidationError, match="repair belongs"):
            self._record(repairs=[repair])

    def test_rejects_invalid_migration_id(self) -> None:
        with pytest.raises(ValidationError):
            self._record(migration_id="../MIG-001")
