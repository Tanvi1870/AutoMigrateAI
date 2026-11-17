"""Persistent migration storage: one JSON file per migration under DATA_DIR/migrations/.

Migrations survive backend restarts, and migration IDs are never reused: the next ID continues after every
ID already stored *and* every project folder already present under generated/<target>/, so a new migration
can never write into (or be traceable to) the files of an older one.

Single-process only: run one backend worker per DATA_DIR.
"""

import logging
import os
import re
import threading
from pathlib import Path

from pydantic import ValidationError

from app.models.migration_record import MigrationRecord
from app.storage.migration_store import MigrationNotFoundError, MigrationStore

log = logging.getLogger(__name__)
_NUMBERED_ID = re.compile(r"^MIG-(\d+)$")
_FILE_MODE = 0o600  # records contain the uploaded test data, including sensitive values


class MigrationStoreError(RuntimeError):
    """A stored migration record could not be read."""


class JsonFileMigrationStore(MigrationStore):
    def __init__(self, directory: Path, generated_dir: Path | None = None) -> None:
        super().__init__()
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._generated_dir = Path(generated_dir) if generated_dir is not None else None
        self._lock = threading.Lock()
        self._cache: dict[str, MigrationRecord] = {}
        self._last_number = self._highest_used_number()

    def next_id(self) -> str:
        with self._lock:
            self._last_number = max(self._last_number, self._highest_used_number()) + 1
            return f"MIG-{self._last_number:03d}"

    def save(self, record: MigrationRecord) -> None:
        payload = record.model_dump_json(by_alias=True, exclude_computed_fields=True, indent=2)
        with self._lock:
            path = self._path(record.migration_id)
            temporary = path.with_suffix(".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            temporary.replace(path)
            self._cache[record.migration_id] = record

    def get(self, migration_id: str) -> MigrationRecord:
        with self._lock:
            cached = self._cache.get(migration_id)
            if cached is not None:
                return cached
            path = self._path(migration_id)
            if not path.is_file():
                raise MigrationNotFoundError(migration_id)
            try:
                record = MigrationRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                log.exception("Stored migration %s could not be read from %s", migration_id, path)
                raise MigrationStoreError(f"Stored migration {migration_id} could not be read") from exc
            self._cache[migration_id] = record
            return record

    def _path(self, migration_id: str) -> Path:
        # IDs are validated (MIG-[A-Za-z0-9]+) by the API before they get here; re-check so a path never escapes.
        if not re.fullmatch(r"MIG-[A-Za-z0-9]{1,32}", migration_id):
            raise MigrationNotFoundError(migration_id)
        return self._directory / f"{migration_id}.json"

    def _highest_used_number(self) -> int:
        names = [path.stem for path in self._directory.glob("MIG-*.json")]
        if self._generated_dir is not None and self._generated_dir.is_dir():
            names += [folder.name for target in self._generated_dir.iterdir() if target.is_dir() for folder in target.iterdir()]
        numbers = [int(match.group(1)) for name in names if (match := _NUMBERED_ID.match(name))]
        return max(numbers, default=0)
