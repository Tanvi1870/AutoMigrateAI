from datetime import datetime
from pathlib import PurePosixPath
from typing import Self

from pydantic import Field, PositiveInt, field_validator, model_validator

from app.models.base import CamelModel
from app.models.demo import DemoFault
from app.models.enums import ArtifactKind, TargetFramework
from app.models.fields import MigrationId, Name


class UploadResponse(CamelModel):
    migration_id: MigrationId
    test_case_id: Name
    name: Name
    steps_detected: int = Field(ge=0)
    source_format: Name


class GeneratedArtifact(CamelModel):
    path: str  # POSIX path relative to the generated project root
    kind: ArtifactKind
    content: str
    step_line_map: dict[PositiveInt, PositiveInt] = Field(default_factory=dict)  # step sequence -> line

    @field_validator("path")
    @classmethod
    def _check_relative_path(cls, path: str) -> str:
        if "\\" in path or ":" in path:
            raise ValueError("artifact paths must be POSIX paths without drive letters")
        parts = PurePosixPath(path).parts
        if not parts or path.startswith("/") or ".." in parts:
            raise ValueError("artifact paths must be relative and stay inside the generated project")
        return path


class MigrationResult(CamelModel):
    migration_id: MigrationId
    test_case_id: Name
    target_framework: TargetFramework
    output_dir: Name  # server-side location; internal only, see GeneratedProject for the API view
    artifacts: list[GeneratedArtifact] = Field(min_length=1)
    generated_at: datetime

    @model_validator(mode="after")
    def _check_artifacts(self) -> Self:
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        if not any(artifact.kind is ArtifactKind.TEST for artifact in self.artifacts):
            raise ValueError("a migration result must contain at least one TEST artifact")
        return self


class GeneratedProject(CamelModel):
    """API view of a generated project: paths are relative to the generated workspace."""

    migration_id: MigrationId
    test_case_id: Name
    target_framework: TargetFramework
    project_path: Name  # e.g. "playwright/MIG-001"
    artifacts: list[GeneratedArtifact]
    generated_at: datetime
    demo_mode: DemoFault | None = None
