from typing import Any

import pytest
from pydantic import ValidationError

from app.models import (
    ApplicationType,
    ArtifactKind,
    CheckStatus,
    ExecutionStatus,
    GeneratedArtifact,
    MigrationMetrics,
    PlanStatus,
    QTestLink,
    RepairContext,
    RepairResult,
    RepairStatus,
    Severity,
    TargetFramework,
    TestStep,
    TraceabilityRecord,
    ValidationCheck,
    ValidationIssue,
    ValidationResult,
)
from tests.factories import (
    NOW,
    make_artifact,
    make_execution,
    make_plan,
    make_proposal,
    make_result,
    make_test_case,
)


class TestMigrationPlan:
    def test_clean_plan_is_ready(self) -> None:
        plan = make_plan()

        assert plan.status is PlanStatus.READY
        assert plan.blocking_reasons == []

    def test_serialises_derived_status(self) -> None:
        dumped = make_plan(unsupported_keywords=["web_drag"]).model_dump(by_alias=True, mode="json")

        assert dumped["status"] == "BLOCKED"
        assert dumped["blockingReasons"] == ["Unsupported keyword: web_drag"]
        assert dumped["targetFramework"] == "playwright"

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        [
            ("errors", ["Parser warning escalated"], "Parser warning escalated"),
            ("unsupported_keywords", ["web_drag"], "Unsupported keyword: web_drag"),
            ("future_adapter_keywords", ["win_click"], "Keyword requires a future adapter: win_click"),
            ("missing_locators", ["loginButton"], "Missing locator: loginButton"),
            ("unresolved_variables", ["PASSWORD"], "Unresolved test data variable: PASSWORD"),
        ],
    )
    def test_any_blocking_finding_blocks_the_plan(self, field: str, value: list[str], reason: str) -> None:
        plan = make_plan(**{field: value})

        assert plan.status is PlanStatus.BLOCKED
        assert reason in plan.blocking_reasons

    def test_warnings_do_not_block(self) -> None:
        assert make_plan(warnings=["web_wait semantics assumed"]).status is PlanStatus.READY

    @pytest.mark.parametrize("app_type", [ApplicationType.WINDOWS, ApplicationType.TERMINAL_EMULATOR])
    @pytest.mark.parametrize("target", list(TargetFramework))
    def test_future_application_types_are_blocked_for_web_targets(
        self, app_type: ApplicationType, target: TargetFramework
    ) -> None:
        plan = make_plan(application_type=app_type, target_framework=target)

        assert plan.status is PlanStatus.BLOCKED
        assert f"does not support {app_type} applications" in plan.blocking_reasons[0]

    def test_rejects_more_mapped_than_detected_keywords(self) -> None:
        with pytest.raises(ValidationError, match="keywords_mapped"):
            make_plan(keywords_detected=3, keywords_mapped=4)

    @pytest.mark.parametrize("field", ["steps_detected", "locators_resolved", "data_parameters", "reusable_blocks"])
    def test_rejects_negative_counts(self, field: str) -> None:
        with pytest.raises(ValidationError):
            make_plan(**{field: -1})

    @pytest.mark.parametrize("migration_id", ["MIG-", "../MIG-1", "mig-001", "MIG-001/x"])
    def test_rejects_invalid_migration_id(self, migration_id: str) -> None:
        with pytest.raises(ValidationError):
            make_plan(migration_id=migration_id)


class TestGeneratedArtifactAndMigrationResult:
    @pytest.mark.parametrize("path", ["tests/login_test.py", "pytest.ini", "testdata/login.json"])
    def test_accepts_relative_paths(self, path: str) -> None:
        assert make_artifact(path).path == path

    @pytest.mark.parametrize(
        "path", ["", "/etc/passwd", "../outside.py", "tests/../../x.py", "C:/x.py", "tests\\login_test.py"]
    )
    def test_rejects_unsafe_paths(self, path: str) -> None:
        with pytest.raises(ValidationError):
            make_artifact(path)

    def test_step_line_map_requires_positive_numbers(self) -> None:
        assert make_artifact(step_line_map={1: 12, 2: 15}).step_line_map == {1: 12, 2: 15}
        with pytest.raises(ValidationError):
            make_artifact(step_line_map={0: 12})
        with pytest.raises(ValidationError):
            make_artifact(step_line_map={1: 0})

    def test_step_line_map_round_trips_through_json(self) -> None:
        artifact = make_artifact(step_line_map={1: 12})

        assert GeneratedArtifact.model_validate_json(artifact.model_dump_json(by_alias=True)) == artifact

    def test_valid_result(self) -> None:
        assert len(make_result().artifacts) == 2

    def test_requires_artifacts(self) -> None:
        with pytest.raises(ValidationError):
            make_result(artifacts=[])

    def test_requires_a_test_artifact(self) -> None:
        with pytest.raises(ValidationError, match="TEST artifact"):
            make_result(artifacts=[make_artifact("pages/login_page.py", ArtifactKind.PAGE_OBJECT)])

    def test_rejects_duplicate_paths(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            make_result(artifacts=[make_artifact(), make_artifact()])


class TestValidationResult:
    def test_all_skipped_by_default(self) -> None:
        assert ValidationResult().status is CheckStatus.SKIPPED

    @pytest.mark.parametrize(
        ("checks", "expected"),
        [
            ({"syntax": CheckStatus.PASS}, CheckStatus.PASS),
            ({check.value: CheckStatus.PASS for check in ValidationCheck}, CheckStatus.PASS),
            ({"syntax": CheckStatus.PASS, "standards": CheckStatus.WARN}, CheckStatus.WARN),
            ({"syntax": CheckStatus.FAIL, "standards": CheckStatus.WARN}, CheckStatus.FAIL),
        ],
    )
    def test_status_is_derived_from_checks(self, checks: dict[str, CheckStatus], expected: CheckStatus) -> None:
        result = ValidationResult(**checks)

        assert result.status is expected
        assert result.model_dump(by_alias=True)["status"] is expected

    def test_passed_accepts_warnings_but_not_failures(self) -> None:
        assert ValidationResult(syntax=CheckStatus.WARN).passed is True
        assert ValidationResult(syntax=CheckStatus.FAIL).passed is False
        assert ValidationResult().passed is False

    def test_every_check_has_a_status_field(self) -> None:
        result = ValidationResult()

        assert all(result.check_status(check) is CheckStatus.SKIPPED for check in ValidationCheck)

    def test_error_issue_requires_failed_check(self) -> None:
        issue = ValidationIssue(check=ValidationCheck.LOCATORS, severity=Severity.ERROR, message="Missing loginButton")

        with pytest.raises(ValidationError, match="ERROR issue"):
            ValidationResult(locators=CheckStatus.PASS, issues=[issue])
        assert ValidationResult(locators=CheckStatus.FAIL, issues=[issue]).status is CheckStatus.FAIL

    def test_warning_issue_requires_warn_or_fail_check(self) -> None:
        issue = ValidationIssue(check=ValidationCheck.STANDARDS, severity=Severity.WARNING, message="Line too long")

        with pytest.raises(ValidationError, match="WARNING issue"):
            ValidationResult(standards=CheckStatus.PASS, issues=[issue])
        assert ValidationResult(standards=CheckStatus.WARN, issues=[issue]).status is CheckStatus.WARN

    def test_info_issue_is_allowed_on_passing_check(self) -> None:
        issue = ValidationIssue(check=ValidationCheck.SYNTAX, severity=Severity.INFO, message="Formatted")

        assert ValidationResult(syntax=CheckStatus.PASS, issues=[issue]).status is CheckStatus.PASS

    def test_issue_validation(self) -> None:
        with pytest.raises(ValidationError, match="requires a file"):
            ValidationIssue(check=ValidationCheck.SYNTAX, severity=Severity.ERROR, message="x", line=3)
        with pytest.raises(ValidationError):
            ValidationIssue(check=ValidationCheck.SYNTAX, severity=Severity.ERROR, message="   ")
        with pytest.raises(ValidationError):
            ValidationIssue(check="spelling", severity=Severity.ERROR, message="x")


class TestExecutionResult:
    def test_passing_execution(self) -> None:
        result = make_execution()

        assert result.passed is True
        assert result.attempt == 1

    @pytest.mark.parametrize(
        "details", [{"failed_step": 2}, {"error_type": "AssertionError"}, {"error_message": "boom"}]
    )
    def test_passing_execution_rejects_failure_details(self, details: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="must not carry failure details"):
            make_execution(**details)

    @pytest.mark.parametrize("status", [ExecutionStatus.FAIL, ExecutionStatus.ERROR, ExecutionStatus.TIMEOUT])
    def test_non_passing_execution_requires_error(self, status: ExecutionStatus) -> None:
        assert make_execution(status).passed is False
        with pytest.raises(ValidationError, match="must include error"):
            make_execution(status, error_type=None, error_message=None)

    @pytest.mark.parametrize("overrides", [{"attempt": 0}, {"duration_seconds": -0.1}])
    def test_rejects_invalid_numbers(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            make_execution(**overrides)


class TestRepair:
    def test_proposal_must_change_value(self) -> None:
        with pytest.raises(ValidationError, match="must change"):
            make_proposal(new_value="[id=\"loginBtn\"]")

    def test_proposal_may_add_wait_from_empty_value(self) -> None:
        assert make_proposal(change_type="WAIT", old_value="", new_value="5000").new_value == "5000"

    @pytest.mark.parametrize(
        "overrides", [{"confidence": 1.5}, {"confidence": -0.5}, {"diagnosis": "  "}, {"change_type": "REWRITE"}]
    )
    def test_proposal_rejects_invalid_fields(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            make_proposal(**overrides)

    def _result(self, status: RepairStatus, **overrides: Any) -> RepairResult:
        fields: dict[str, Any] = {
            "migration_id": "MIG-001",
            "target_framework": TargetFramework.PLAYWRIGHT,
            "proposal": make_proposal(),
            "status": status,
            "created_at": NOW,
        }
        return RepairResult(**(fields | overrides))

    def test_proposed_repair_is_not_applied(self) -> None:
        assert self._result(RepairStatus.PROPOSED).applied is False

    @pytest.mark.parametrize("status", [RepairStatus.PROPOSED, RepairStatus.REJECTED])
    def test_unapplied_repair_cannot_have_outcomes(self, status: RepairStatus) -> None:
        with pytest.raises(ValidationError):
            self._result(status, re_execution=make_execution())
        with pytest.raises(ValidationError):
            self._result(status, validation_status=CheckStatus.PASS)

    def test_rejected_repair_may_record_failed_validation(self) -> None:
        assert self._result(RepairStatus.REJECTED, validation_status=CheckStatus.FAIL).applied is False

    def test_applied_repair_without_rerun_is_valid(self) -> None:
        result = self._result(RepairStatus.APPLIED, validation_status=CheckStatus.PASS)

        assert result.applied is True

    def test_verified_repair_requires_passing_rerun(self) -> None:
        with pytest.raises(ValidationError, match="requires a re-execution"):
            self._result(RepairStatus.PASSED_AFTER_REPAIR)
        with pytest.raises(ValidationError, match="passing re-execution"):
            self._result(RepairStatus.PASSED_AFTER_REPAIR, re_execution=make_execution(ExecutionStatus.FAIL))

        verified = self._result(RepairStatus.PASSED_AFTER_REPAIR, re_execution=make_execution(attempt=2))
        assert verified.applied is True

    def test_failed_repair_requires_failing_rerun(self) -> None:
        with pytest.raises(ValidationError, match="cannot have a passing"):
            self._result(RepairStatus.FAILED, re_execution=make_execution())

        assert self._result(RepairStatus.FAILED, re_execution=make_execution(ExecutionStatus.FAIL)).applied

    def test_rerun_target_must_match(self) -> None:
        with pytest.raises(ValidationError, match="target"):
            self._result(RepairStatus.PASSED_AFTER_REPAIR, re_execution=make_execution(target=TargetFramework.SELENIUM))

    def test_context_requires_failed_execution_and_matching_locator(self) -> None:
        test_case = make_test_case()
        step = test_case.steps[3]
        locator = test_case.object_repository["loginButton"]
        failed = make_execution(ExecutionStatus.FAIL)

        assert RepairContext(step=step, locator=locator, generated_code="...", execution=failed).step == step
        with pytest.raises(ValidationError, match="failed execution"):
            RepairContext(step=step, locator=locator, generated_code="...", execution=make_execution())
        with pytest.raises(ValidationError, match="does not belong"):
            RepairContext(
                step=TestStep(sequence=9, keyword="web_click", object_name="other"),
                locator=locator,
                generated_code="...",
                execution=failed,
            )


class TestTraceability:
    def test_record_uses_qtest_alias(self) -> None:
        record = TraceabilityRecord(
            source_test_case_id="ICAP-TC-001",
            source_step_ref="BLK-LOGIN#3",
            step_sequence=4,
            keyword="web_click",
            target_framework=TargetFramework.PLAYWRIGHT,
            generated_file="tests/login_test.py",
            generated_line=18,
            execution_status=ExecutionStatus.PASS,
            qtest_case_id="QT-1001",
        )

        dumped = record.model_dump(by_alias=True, mode="json")
        assert dumped["qTestCaseId"] == "QT-1001"
        assert dumped["sourceStepRef"] == "BLK-LOGIN#3"
        assert TraceabilityRecord.model_validate(dumped) == record

    def test_line_requires_file(self) -> None:
        with pytest.raises(ValidationError, match="requires a generated file"):
            TraceabilityRecord(source_test_case_id="TC", step_sequence=1, keyword="web_click", generated_line=3)

    def test_qtest_link_accepts_alias_and_field_name(self) -> None:
        by_alias = QTestLink.model_validate(
            {"sourceTestCaseId": "ICAP-TC-001", "generatedScript": "login_test.py", "qTestCaseId": "QT-1001"}
        )
        by_name = QTestLink(source_test_case_id="ICAP-TC-001", generated_script="login_test.py", qtest_case_id="QT-1001")

        assert by_alias == by_name
        assert by_name.model_dump(by_alias=True)["qTestCaseId"] == "QT-1001"


class TestMigrationMetrics:
    def test_computes_saving_from_measured_timings(self) -> None:
        metrics = MigrationMetrics(manual_estimate_minutes=45, ai_migration_seconds=300)

        assert metrics.observed_time_saving_percent == 88.9
        assert metrics.model_dump(by_alias=True)["observedTimeSavingPercent"] == 88.9

    def test_saving_can_be_negative_when_ai_is_slower(self) -> None:
        assert MigrationMetrics(manual_estimate_minutes=1, ai_migration_seconds=120).observed_time_saving_percent == -100.0

    def test_saving_is_unknown_without_both_timings(self) -> None:
        assert MigrationMetrics(manual_estimate_minutes=45).observed_time_saving_percent is None
        assert MigrationMetrics().observed_time_saving_percent is None

    def test_rejects_zero_manual_estimate(self) -> None:
        with pytest.raises(ValidationError):
            MigrationMetrics(manual_estimate_minutes=0, ai_migration_seconds=10)
