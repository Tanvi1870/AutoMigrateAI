from typing import Self

from pydantic import Field, computed_field, model_validator

from app.models.base import CamelModel
from app.models.enums import CheckStatus, Severity, TargetFramework, ValidationCheck
from app.models.fields import Name, Text

CHECK_LABELS = {
    ValidationCheck.SYNTAX: "Python syntax",
    ValidationCheck.IMPORTS: "Imports",
    ValidationCheck.KEYWORDS: "Keywords",
    ValidationCheck.LOCATORS: "Locators",
    ValidationCheck.VARIABLES: "Test data variables",
    ValidationCheck.ASSERTIONS: "Assertions",
    ValidationCheck.DUPLICATES: "Duplicate steps",
    ValidationCheck.FRAMEWORK: "Framework compatibility",
    ValidationCheck.STANDARDS: "Coding standards",
}


class ValidationIssue(CamelModel):
    check: ValidationCheck
    severity: Severity
    message: Text
    step_sequence: int | None = Field(default=None, ge=1)
    file: Name | None = None
    line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_location(self) -> Self:
        if self.line is not None and self.file is None:
            raise ValueError("an issue line number requires a file")
        return self

    @property
    def reason(self) -> str:
        """One-line, human-readable reason, e.g. 'tests/login_test.py:12 (step 4): Missing locator ...'."""
        where = "" if self.file is None else self.file if self.line is None else f"{self.file}:{self.line}"
        if self.step_sequence is not None:
            where = f"{where} (step {self.step_sequence})" if where else f"Step {self.step_sequence}"
        return f"{where}: {self.message}" if where else self.message


class CheckSummary(CamelModel):
    check: ValidationCheck
    label: str
    status: CheckStatus
    reasons: list[str] = Field(default_factory=list)


class ValidationResult(CamelModel):
    """Overall status is derived from the per-check results, which must agree with the issues."""

    target_framework: TargetFramework | None = None
    syntax: CheckStatus = CheckStatus.SKIPPED
    imports: CheckStatus = CheckStatus.SKIPPED
    keywords: CheckStatus = CheckStatus.SKIPPED
    locators: CheckStatus = CheckStatus.SKIPPED
    variables: CheckStatus = CheckStatus.SKIPPED
    assertions: CheckStatus = CheckStatus.SKIPPED
    duplicates: CheckStatus = CheckStatus.SKIPPED
    framework: CheckStatus = CheckStatus.SKIPPED
    standards: CheckStatus = CheckStatus.SKIPPED
    issues: list[ValidationIssue] = Field(default_factory=list)

    def check_status(self, check: ValidationCheck) -> CheckStatus:
        return getattr(self, check.value)

    @model_validator(mode="after")
    def _check_issue_consistency(self) -> Self:
        for issue in self.issues:
            status = self.check_status(issue.check)
            if issue.severity is Severity.ERROR and status is not CheckStatus.FAIL:
                raise ValueError(f"'{issue.check}' has an ERROR issue but its status is {status}")
            if issue.severity is Severity.WARNING and status not in (CheckStatus.WARN, CheckStatus.FAIL):
                raise ValueError(f"'{issue.check}' has a WARNING issue but its status is {status}")
        return self

    @computed_field
    @property
    def status(self) -> CheckStatus:
        statuses = {self.check_status(check) for check in ValidationCheck}
        if CheckStatus.FAIL in statuses:
            return CheckStatus.FAIL
        if CheckStatus.WARN in statuses:
            return CheckStatus.WARN
        if statuses == {CheckStatus.SKIPPED}:
            return CheckStatus.SKIPPED
        return CheckStatus.PASS

    @computed_field
    @property
    def checks(self) -> list[CheckSummary]:
        """Per-check status with its ERROR/WARNING reasons, in display order."""
        return [
            CheckSummary(
                check=check,
                label=CHECK_LABELS[check],
                status=self.check_status(check),
                reasons=[
                    issue.reason for issue in self.issues if issue.check is check and issue.severity is not Severity.INFO
                ],
            )
            for check in ValidationCheck
        ]

    @computed_field
    @property
    def summary(self) -> str:
        errors = sum(issue.severity is Severity.ERROR for issue in self.issues)
        warnings = sum(issue.severity is Severity.WARNING for issue in self.issues)
        if self.status is CheckStatus.SKIPPED:
            return "SKIPPED: no checks were run"
        if self.status is CheckStatus.PASS:
            return "PASS: all checks passed"
        counts = [f"{n} {word}{'' if n == 1 else 's'}" for n, word in ((errors, "error"), (warnings, "warning")) if n]
        return f"{self.status}: {', '.join(counts)}"

    @property
    def passed(self) -> bool:
        return self.status in (CheckStatus.PASS, CheckStatus.WARN)
