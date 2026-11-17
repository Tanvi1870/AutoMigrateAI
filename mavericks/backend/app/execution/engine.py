import threading
from collections.abc import Mapping
from pathlib import Path

from app.execution.runner import BaseRunner
from app.models.enums import TargetFramework
from app.models.execution_result import ExecutionResult


class ExecutionCapacityError(RuntimeError):
    """All execution slots are busy; the caller should retry later (HTTP 429)."""


class ExecutionEngine:
    """Selects the runner for a target; runners only execute generated projects inside the workspace.

    Each run starts pytest and a browser, so at most `max_concurrent` runs execute at once; further requests
    are refused immediately instead of queueing threads and browsers without bound.
    """

    def __init__(self, runners: Mapping[TargetFramework, BaseRunner], max_concurrent: int | None = None) -> None:
        for target, runner in runners.items():
            if runner.target is not target:
                raise ValueError(f"{type(runner).__name__} is registered for {target} but runs {runner.target}")
        if max_concurrent is not None and max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._runners = dict(runners)
        self._max_concurrent = max_concurrent
        self._slots = threading.BoundedSemaphore(max_concurrent) if max_concurrent else None

    def execute(self, target: TargetFramework, project_dir: Path, attempt: int = 1) -> ExecutionResult:
        runner = self._runner(target)
        if self._slots is None:
            return runner.run(project_dir, attempt)
        if not self._slots.acquire(blocking=False):
            raise ExecutionCapacityError(
                f"All {self._max_concurrent} test execution slot(s) are busy; try again when a run has finished"
            )
        try:
            return runner.run(project_dir, attempt)
        finally:
            self._slots.release()

    def resolve_project(self, target: TargetFramework, project_dir: Path) -> Path:
        """The project path if it is inside the target's controlled workspace; raises otherwise."""
        return self._runner(target).resolve_project(project_dir)

    def _runner(self, target: TargetFramework) -> BaseRunner:
        runner = self._runners.get(target)
        if runner is None:
            raise ValueError(f"No runner is registered for {target}")
        return runner
