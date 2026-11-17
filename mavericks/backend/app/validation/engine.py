"""ValidationEngine: static checks on a generated project before it is executed."""

import ast
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import ClassVar

from app.models.canonical_test import CanonicalTestCase
from app.models.enums import CheckStatus, Severity, TargetFramework, ValidationCheck
from app.models.migration_result import GeneratedArtifact
from app.models.validation_result import ValidationIssue, ValidationResult

PROJECT_SUFFIXES = frozenset({".py", ".txt", ".ini", ".json"})
_SKIPPED_DIRS = frozenset({"__pycache__", "artifacts", ".pytest_cache", ".venv", "venv"})
_STEP_MARKER = re.compile(r"^\s*#\s*\[AM-STEP (\d+)\]")


@dataclass
class ValidationContext:
    test_case: CanonicalTestCase
    target: TargetFramework
    files: dict[str, str]  # POSIX path relative to the project root -> text
    trees: dict[str, ast.Module] = field(default_factory=dict)  # .py files that parsed

    def __post_init__(self) -> None:
        for path, source in self.files.items():
            if path.endswith(".py") and path not in self.trees:
                try:
                    self.trees[path] = ast.parse(source, filename=path)
                except SyntaxError:
                    continue  # reported by SyntaxValidator

    @property
    def test_files(self) -> list[str]:
        return sorted(path for path in self.files if is_test_file(path))

    @property
    def test_trees(self) -> dict[str, ast.Module]:
        return {path: self.trees[path] for path in self.test_files if path in self.trees}

    def step_markers(self, path: str) -> list[tuple[int, int]]:
        return step_markers(self.files[path])


def step_markers(source: str) -> list[tuple[int, int]]:
    """(step sequence, line) for every `# [AM-STEP n]` marker in a file."""
    markers = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        if match := _STEP_MARKER.match(line):
            markers.append((int(match.group(1)), line_no))
    return markers


def is_test_file(path: str) -> bool:
    posix = PurePosixPath(path)
    return (
        posix.parts[0] == "tests"
        and posix.suffix == ".py"
        and (posix.name.startswith("test_") or posix.stem.endswith("_test"))
    )


def issue(
    check: ValidationCheck,
    severity: Severity,
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    step: int | None = None,
) -> ValidationIssue:
    return ValidationIssue(check=check, severity=severity, message=message, file=file, line=line, step_sequence=step)


class Validator(ABC):
    """One validator owns one or more checks and reports issues only for those checks."""

    checks: ClassVar[tuple[ValidationCheck, ...]]

    @abstractmethod
    def validate(self, context: ValidationContext) -> list[ValidationIssue]: ...


class ValidationEngine:
    def __init__(self, validators: Sequence[Validator]) -> None:
        self._validators = list(validators)

    def validate(self, project_dir: Path, test_case: CanonicalTestCase, target: TargetFramework) -> ValidationResult:
        """Validate the project as written to disk, i.e. exactly what will be executed."""
        return self.validate_files(read_project(project_dir), test_case, target)

    def validate_artifacts(
        self, artifacts: Iterable[GeneratedArtifact], test_case: CanonicalTestCase, target: TargetFramework
    ) -> ValidationResult:
        return self.validate_files({a.path: a.content for a in artifacts}, test_case, target)

    def validate_files(
        self, files: Mapping[str, str], test_case: CanonicalTestCase, target: TargetFramework
    ) -> ValidationResult:
        context = ValidationContext(test_case=test_case, target=target, files=dict(files))
        statuses: dict[str, CheckStatus] = {}
        issues: list[ValidationIssue] = []
        for validator in self._validators:
            found = validator.validate(context)
            unowned = {i.check for i in found} - set(validator.checks)
            if unowned:
                raise ValueError(f"{type(validator).__name__} reported checks it does not own: {sorted(unowned)}")
            issues += found
            for check in validator.checks:
                statuses[check.value] = _status(i for i in issues if i.check is check)
        return ValidationResult(target_framework=target, issues=issues, **statuses)


def _status(issues: Iterable[ValidationIssue]) -> CheckStatus:
    severities = {i.severity for i in issues}
    if Severity.ERROR in severities:
        return CheckStatus.FAIL
    if Severity.WARNING in severities:
        return CheckStatus.WARN
    return CheckStatus.PASS


def read_project(project_dir: Path) -> dict[str, str]:
    root = project_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Generated project not found: {project_dir}")
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or path.suffix not in PROJECT_SUFFIXES
            or any(part in _SKIPPED_DIRS or part.startswith(".") for part in relative.parts)
            or not path.resolve().is_relative_to(root)  # never follow links out of the project
        ):
            continue
        files[relative.as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return files
