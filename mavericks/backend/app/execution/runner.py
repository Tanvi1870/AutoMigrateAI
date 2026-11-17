"""Runs a generated pytest project in a subprocess with a fixed command line, inside the generated workspace only.

Nothing from user input reaches the command line: the command, its arguments and the environment are built
here, and the project directory must resolve inside `generated/<target>/`.
"""

import os
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from app.generators.scaffold.navigation_policy import ENV_VAR as NAVIGATION_ENV_VAR
from app.generators.scaffold.navigation_policy import NavigationPolicy
from app.models.enums import ExecutionStatus, TargetFramework
from app.models.execution_result import ExecutionResult
from app.validation.engine import step_markers

RUNS_DIR = "artifacts/runs"
MAX_OUTPUT_CHARS = 200_000
MAX_MESSAGE_CHARS = 2_000
KILL_GRACE_SECONDS = 10
# The only server environment variables a generated test run receives (H-03: allowlist, not denylist).
_ENV_ALLOWLIST = frozenset(
    {
        # Operating system and locale
        "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "OS", "TEMP", "TMP", "TMPDIR",
        "HOME", "USERPROFILE", "USERNAME", "USER", "LOGNAME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
        "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LANGUAGE", "TZ",
        # Display (headed runs) and network trust settings the browsers need
        "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
        "VIRTUAL_ENV",
        # Browser and driver locations; AutoMigrate run settings read by the generated config/settings.py
        "PLAYWRIGHT_BROWSERS_PATH", "AM_CHROME_BINARY", "AM_BROWSER_ARGS", "AM_TIMEOUT_MS",
        "AM_NAVIGATION_TIMEOUT_MS", "AM_VIEWPORT_WIDTH", "AM_VIEWPORT_HEIGHT",
    }
)
_ENV_ALLOWED_PREFIXES = ("LC_", "XDG_", "SE_", "AM_DATA_")
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_TRACEBACK_LOCATION = re.compile(r"(tests[\\/][\w.\\/-]+\.py):(\d+)")
_ERROR_TYPE = re.compile(r"^(?:[\w]+\.)*(\w+(?:Error|Exception|Timeout\w*)):")
_EXIT_CODE_ERRORS = {
    2: "Test run was interrupted",
    3: "Internal pytest error",
    4: "pytest usage error",
    5: "No tests were collected",
}


class ProjectNotAllowedError(ValueError):
    """The directory is not a generated pytest project inside the controlled workspace."""


@dataclass(frozen=True)
class ProcessOutput:
    exit_code: int | None  # None when the process was killed
    stdout: str
    stderr: str
    timed_out: bool = False


ProcessRunner = Callable[[Sequence[str], Path, Mapping[str, str], float], ProcessOutput]


@dataclass(frozen=True)
class _Problem:
    kind: str  # "failure" (assertion/test error) or "error" (setup/teardown error)
    error_type: str
    message: str
    details: str


def _passes_to_tests(name: str) -> bool:
    upper = name.upper()
    if upper.startswith("AM_DATA_"):
        return True  # test-data overrides; navigation URLs can never contain sensitive ones (NavigationGuard)
    if any(marker in upper for marker in _SECRET_ENV_MARKERS):
        return False
    return upper in _ENV_ALLOWLIST or upper.startswith(_ENV_ALLOWED_PREFIXES)


def run_process(command: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> ProcessOutput:
    """Run without a shell; on timeout kill the whole process tree (pytest, browser drivers, browsers)."""
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(env),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **group,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return ProcessOutput(process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            return ProcessOutput(None, stdout or "", stderr or "", timed_out=True)


def _kill_tree(process: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            subprocess.run([str(taskkill), "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.kill()
    except OSError:
        pass


class BaseRunner(ABC):
    """Runs one target's generated pytest projects. Never accepts commands or arguments from user input."""

    target: ClassVar[TargetFramework]

    def __init__(
        self,
        generated_root: Path,
        timeout_seconds: float,
        headless: bool,
        process_runner: ProcessRunner = run_process,
        allowed_urls: Sequence[str] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._workspace = (Path(generated_root) / self.target).resolve()
        self._timeout_seconds = timeout_seconds
        self._headless = headless
        self._process_runner = process_runner
        # Navigation allowlist the generated conftest enforces at run time (validated here, at startup).
        self._navigation = NavigationPolicy(list(allowed_urls)) if allowed_urls is not None else None

    @abstractmethod
    def browser_args(self) -> list[str]:
        """Fixed pytest arguments for this target's browser options."""

    def command(self, run_dir: Path) -> list[str]:
        return [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--junitxml={run_dir / 'junit.xml'}",
            "-o",
            f"log_file={run_dir / 'test-run.log'}",
            *self.browser_args(),
        ]

    def resolve_project(self, project_dir: Path) -> Path:
        project = Path(project_dir).resolve()
        if project == self._workspace or not project.is_relative_to(self._workspace):
            raise ProjectNotAllowedError(f"{project_dir} is outside the {self.target} workspace")
        if not (project / "pytest.ini").is_file() or not (project / "tests").is_dir():
            raise ProjectNotAllowedError(f"{project_dir} is not a generated pytest project")
        return project

    def run(self, project_dir: Path, attempt: int = 1) -> ExecutionResult:
        project = self.resolve_project(project_dir)
        started_at = datetime.now(UTC)
        run_dir = project / RUNS_DIR / f"{started_at:%Y%m%dT%H%M%S%f}-attempt{attempt}"
        run_dir.mkdir(parents=True)
        start = time.monotonic()
        try:
            output = self._process_runner(self.command(run_dir), project, self._environment(run_dir), self._timeout_seconds)
        except OSError as exc:
            output = ProcessOutput(None, "", f"Could not start pytest: {type(exc).__name__}: {exc}")
        duration = time.monotonic() - start
        return self._result(project, run_dir, output, started_at, duration, attempt)

    def _environment(self, run_dir: Path) -> dict[str, str]:
        """An allowlisted environment: only what Python, pytest and the browsers need, plus AM_* test settings.

        Everything else in the server's environment (cloud credentials, connection strings, API keys) is left out.
        """
        env = {name: value for name, value in os.environ.items() if _passes_to_tests(name)}
        if self._navigation is not None:
            env[NAVIGATION_ENV_VAR] = self._navigation.to_environment()
        return env | {
            "AM_ARTIFACTS_DIR": str(run_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }

    def _result(
        self,
        project: Path,
        run_dir: Path,
        output: ProcessOutput,
        started_at: datetime,
        duration: float,
        attempt: int,
    ) -> ExecutionResult:
        total, failed, problem = _read_junit(run_dir / "junit.xml")
        screenshot_dir, dom_dir = run_dir / "screenshots", run_dir / "dom"
        details: dict[str, object] = {}
        if output.timed_out:
            status = ExecutionStatus.TIMEOUT
            details = {
                "error_type": "Timeout",
                "error_message": f"Test run exceeded {self._timeout_seconds:g}s and was stopped",
            }
        elif output.exit_code == 0:
            status = ExecutionStatus.PASS
        elif output.exit_code == 1 and problem is not None:
            status = ExecutionStatus.FAIL if problem.kind == "failure" else ExecutionStatus.ERROR
            details = {
                "error_type": problem.error_type,
                "error_message": problem.message,
                "failed_step": _failed_step(project, problem.details),
            }
        else:
            status = ExecutionStatus.FAIL if output.exit_code == 1 else ExecutionStatus.ERROR
            if output.exit_code is None:
                reason = "pytest did not run"
            else:
                reason = _EXIT_CODE_ERRORS.get(output.exit_code, f"pytest exited with code {output.exit_code}")
            last_line = _last_line(output.stderr) or _last_line(output.stdout)
            details = {
                "error_type": "TestFailure" if output.exit_code == 1 else "ExecutionError",
                "error_message": _clip(f"{reason}: {last_line}" if last_line else reason, MAX_MESSAGE_CHARS),
            }
        return ExecutionResult(
            status=status,
            target_framework=self.target,
            started_at=started_at,
            duration_seconds=round(duration, 3),
            attempt=attempt,
            stdout=_tail(output.stdout),
            stderr=_tail(output.stderr),
            exit_code=output.exit_code,
            logs=_tail(_read_text(run_dir / "test-run.log")),
            tests_total=total,
            tests_failed=failed,
            screenshots=sorted(p.relative_to(project).as_posix() for p in screenshot_dir.glob("*.png"))
            if screenshot_dir.is_dir()
            else [],
            dom_snapshots=sorted(p.relative_to(project).as_posix() for p in dom_dir.glob("*.html"))
            if dom_dir.is_dir()
            else [],
            artifacts_dir=run_dir.relative_to(project).as_posix(),
            **details,
        )


def _read_junit(path: Path) -> tuple[int, int, _Problem | None]:
    """(tests, failed or errored tests, first problem) from pytest's JUnit XML; a missing/broken report is empty."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return 0, 0, None
    total = failed = 0
    first: _Problem | None = None
    for case in root.iter("testcase"):
        total += 1
        element = next((e for e in (case.find("failure"), case.find("error")) if e is not None), None)
        if element is None:
            continue
        failed += 1
        if first is None:
            message = element.get("message") or _last_line(element.text or "") or element.tag
            first = _Problem(
                kind=element.tag,
                error_type=element.get("type") or _error_type(message),
                message=_clip(message, MAX_MESSAGE_CHARS),
                details=element.text or "",
            )
    return total, failed, first


def _error_type(message: str) -> str:
    if match := _ERROR_TYPE.match(message):
        return match.group(1)
    return "AssertionError" if message.startswith("assert") else "TestFailure"


def _failed_step(project: Path, details: str) -> int | None:
    """Map the innermost traceback line in a generated test file to its `# [AM-STEP n]` marker."""
    for file, line in reversed(_TRACEBACK_LOCATION.findall(details)):
        path = (project / file.replace("\\", "/")).resolve()
        if not path.is_relative_to(project) or not path.is_file():
            continue
        steps = [sequence for sequence, marker_line in step_markers(_read_text(path)) if marker_line <= int(line)]
        if steps:
            return steps[-1]
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _last_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tail(text: str) -> str:
    """Keep the end of long output, where pytest prints the failure summary."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return f"[... {len(text) - MAX_OUTPUT_CHARS} characters truncated ...]\n" + text[-MAX_OUTPUT_CHARS:]
