from pathlib import Path

from app.models.enums import TargetFramework
from app.models.validation_result import ValidationResult
from app.services.errors import MigrationStateError
from app.services.repair_service import current_test_case
from app.storage.migration_store import MigrationStore
from app.validation.engine import ValidationEngine

__all__ = ["MigrationStateError", "ValidationService"]


class ValidationService:
    def __init__(self, store: MigrationStore, engine: ValidationEngine) -> None:
        self._store = store
        self._engine = engine

    def validate(self, migration_id: str, target: TargetFramework) -> ValidationResult:
        with self._store.operation(migration_id):
            return self._validate(migration_id, target)

    def _validate(self, migration_id: str, target: TargetFramework) -> ValidationResult:
        record = self._store.get(migration_id)
        result = record.results.get(target)
        if result is None:
            raise MigrationStateError(f"Migration {migration_id} has no generated {target} project; generate it first")
        try:
            validation = self._engine.validate(Path(result.output_dir), current_test_case(record, target), target)
        except FileNotFoundError:
            raise MigrationStateError(f"The generated {target} project for {migration_id} is missing") from None
        self._store.save(record.model_copy(update={"validations": {**record.validations, target: validation}}))
        return validation
