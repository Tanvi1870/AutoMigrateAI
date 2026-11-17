import itertools
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager

from app.models.migration_record import MigrationRecord


class MigrationNotFoundError(LookupError):
    def __init__(self, migration_id: str) -> None:
        super().__init__(f"Migration {migration_id} not found")
        self.migration_id = migration_id


class MigrationBusyError(RuntimeError):
    """Another state-changing operation is running on this migration (mapped to HTTP 409)."""

    def __init__(self, migration_id: str) -> None:
        super().__init__(
            f"Migration {migration_id} is busy with another operation (analyze, generate, validate, execute, "
            "repair or qTest sync); try again when it has finished"
        )
        self.migration_id = migration_id


class MigrationStore(ABC):
    """Stores migration records.

    Services read a record, work for a while (an execution can take minutes) and save an updated copy. Every
    such read-modify-write runs inside `operation(migration_id)`, so two requests can never interleave on the
    same migration and overwrite each other's results or generated files.
    """

    def __init__(self) -> None:
        self._operation_locks: dict[str, threading.Lock] = {}
        self._operation_guard = threading.Lock()

    @abstractmethod
    def next_id(self) -> str: ...

    @abstractmethod
    def save(self, record: MigrationRecord) -> None: ...

    @abstractmethod
    def get(self, migration_id: str) -> MigrationRecord: ...

    @contextmanager
    def operation(self, migration_id: str) -> Iterator[None]:
        """Exclusive access for one state-changing operation; fails fast with MigrationBusyError."""
        with self._operation_guard:
            lock = self._operation_locks.setdefault(migration_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise MigrationBusyError(migration_id)
        try:
            yield
        finally:
            lock.release()


class InMemoryMigrationStore(MigrationStore):
    """Process-local store, used by the tests; the application uses the persistent JsonFileMigrationStore."""

    def __init__(self) -> None:
        super().__init__()
        self._records: dict[str, MigrationRecord] = {}
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def next_id(self) -> str:
        with self._lock:
            return f"MIG-{next(self._counter):03d}"

    def save(self, record: MigrationRecord) -> None:
        with self._lock:
            self._records[record.migration_id] = record

    def get(self, migration_id: str) -> MigrationRecord:
        with self._lock:
            try:
                return self._records[migration_id]
            except KeyError:
                raise MigrationNotFoundError(migration_id) from None
