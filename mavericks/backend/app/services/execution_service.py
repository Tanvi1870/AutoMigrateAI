from pathlib import Path

from app.execution.engine import ExecutionEngine
from app.execution.runner import ProjectNotAllowedError
from app.models.enums import TargetFramework
from app.models.execution_result import ExecutionResult
from app.services.errors import MigrationStateError
from app.storage.migration_store import MigrationStore


class ExecutionService:
    def __init__(self, store: MigrationStore, engine: ExecutionEngine) -> None:
        self._store = store
        self._engine = engine

    def execute(self, migration_id: str, target: TargetFramework) -> ExecutionResult:
        """Run the generated project only after it was generated and passed validation (PASS or WARNING)."""
        with self._store.operation(migration_id):
            return self._execute(migration_id, target)

    def _execute(self, migration_id: str, target: TargetFramework) -> ExecutionResult:
        record = self._store.get(migration_id)
        result = record.results.get(target)
        if result is None:
            raise MigrationStateError(f"Migration {migration_id} has no generated {target} project; generate it first")
        validation = record.validations.get(target)
        if validation is None:
            raise MigrationStateError(f"The {target} project for {migration_id} has not been validated; validate it first")
        if not validation.passed:
            raise MigrationStateError(f"The {target} project for {migration_id} failed validation ({validation.summary})")

        attempt = len(record.executions.get(target, [])) + 1
        try:
            execution = self._engine.execute(target, Path(result.output_dir), attempt)
        except ProjectNotAllowedError:
            raise MigrationStateError(f"The generated project is outside the {target} workspace") from None
        executions = {**record.executions, target: [*record.executions.get(target, []), execution]}
        self._store.save(record.model_copy(update={"executions": executions}))
        return execution
