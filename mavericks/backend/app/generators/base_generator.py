"""Shared CanonicalTestCase -> pytest project generation.

Framework calls come only from keyword KB templates; locators only from the object repository. Anything
that cannot be resolved stops generation with a GenerationError instead of producing guessed code.
"""

import json
import re
import sys
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from app.agents.keyword_resolver import KeywordResolver, RenderContext
from app.agents.locator_resolver import LocatorResolver
from app.models.canonical_test import CanonicalTestCase, Locator, TestStep
from app.models.capabilities import SUPPORTED_TARGETS
from app.models.enums import ArtifactKind, TargetFramework
from app.models.locator_resolution import LocatorResolution
from app.models.migration_result import GeneratedArtifact

if TYPE_CHECKING:
    from app.agents.navigation_guard import NavigationGuard

SCAFFOLD_DIR = Path(__file__).with_name("scaffold")
TEST_DATA_FIXTURE = "test_data"
DATA_CLASS = "DataSet"
LOCAL_PACKAGES = frozenset({"config", "pages", "testdata"})
INDENT = "    "
_FROM_IMPORT = re.compile(r"from ([\w.]+) import ([\w, ]+)")
_PLAIN_IMPORT = re.compile(r"import ([\w.]+)")


class GenerationError(ValueError):
    """The test case cannot be converted without guessing; `problems` lists every reason."""

    def __init__(self, test_case_id: str, problems: Iterable[str]) -> None:
        self.problems = list(problems)
        super().__init__(f"Cannot generate {test_case_id}: {'; '.join(self.problems)}")


@dataclass(frozen=True)
class ProjectNames:
    base: str  # snake_case stem, e.g. "login"
    test_file: str

    @classmethod
    def for_test_case(cls, test_case: CanonicalTestCase, test_file_pattern: str) -> "ProjectNames":
        base = _snake(test_case.name) or _snake(test_case.test_case_id) or "migrated"
        base = base.removeprefix("test_").removesuffix("_test") or base
        if base[0].isdigit():
            base = f"tc_{base}"
        return cls(base=base, test_file=test_file_pattern.format(test_name=base))

    @property
    def test_function(self) -> str:
        return f"test_{self.base}"

    @property
    def page_module(self) -> str:
        return f"{self.base}_page"

    @property
    def page_class(self) -> str:
        return "".join(part.capitalize() for part in self.base.split("_")) + "Page"

    @property
    def data_file(self) -> str:
        return f"{self.base}_data.json"

    @property
    def secrets_file(self) -> str:
        return f"{self.base}_secrets.local.json"


class BaseGenerator(ABC):
    """Builds the project layout, page object, test data and step markers; subclasses supply target specifics."""

    target: ClassVar[TargetFramework]

    def __init__(
        self,
        keyword_resolver: KeywordResolver,
        locator_resolver: LocatorResolver,
        framework_templates: dict[str, Any],
        navigation: "NavigationGuard | None" = None,
    ) -> None:
        self._keyword_resolver = keyword_resolver
        self._locator_resolver = locator_resolver
        self._templates = framework_templates["targets"][self.target]
        self._navigation = navigation

    @abstractmethod
    def _locator_literal(self, locator: Locator) -> str:
        """Python literal stored as the page-object constant for this locator."""

    @abstractmethod
    def _render_context(self, locator_ref: str | None, locator: Locator | None) -> RenderContext:
        """Context for KB templates; locator_ref is the page-object constant, e.g. 'LoginPage.USERNAME'."""

    @abstractmethod
    def _conftest(self) -> str:
        """Fixtures for browser/page setup, logging, failure screenshots and teardown."""

    def generate(self, test_case: CanonicalTestCase, output_dir: Path) -> list[GeneratedArtifact]:
        artifacts = self.build(test_case)
        self.write(artifacts, output_dir)
        self.write_secrets(test_case, output_dir)
        return artifacts

    @staticmethod
    def write(artifacts: list[GeneratedArtifact], output_dir: Path) -> None:
        _write(artifacts, output_dir)

    def write_secrets(self, test_case: CanonicalTestCase, output_dir: Path) -> None:
        """Write sensitive test-data values next to the project's data file, outside the artifact list.

        Artifacts are what the API returns, the UI shows and the migration record keeps, so they only hold
        null for sensitive variables. The generated loader reads the values from this local file (an
        AM_DATA_<NAME> environment variable still wins). Without sensitive variables any old file is removed.
        """
        names = ProjectNames.for_test_case(test_case, self._templates["testFilePattern"])
        path = output_dir.resolve() / "testdata" / names.secrets_file
        secrets = _secrets(test_case)
        if not secrets:
            path.unlink(missing_ok=True)
            return
        content = {"testCaseId": test_case.test_case_id, "secrets": secrets}
        _write([_artifact(f"testdata/{names.secrets_file}", ArtifactKind.TEST_DATA, _json(content))], output_dir)

    def build(self, test_case: CanonicalTestCase) -> list[GeneratedArtifact]:
        resolutions = self._check(test_case)
        names = ProjectNames.for_test_case(test_case, self._templates["testFilePattern"])
        constants = _constant_names(r.object_name for r in resolutions)
        locators = {r.object_name: self._locator_resolver.require(r) for r in resolutions}
        test_source, step_line_map = self._test_module(test_case, names, constants, locators)
        return [
            _artifact("tests/__init__.py", ArtifactKind.OTHER, ""),
            GeneratedArtifact(
                path=f"tests/{names.test_file}",
                kind=ArtifactKind.TEST,
                content=test_source,
                step_line_map=step_line_map,
            ),
            _artifact("pages/__init__.py", ArtifactKind.OTHER, ""),
            _artifact(
                f"pages/{names.page_module}.py",
                ArtifactKind.PAGE_OBJECT,
                self._page_module(test_case, names, resolutions, constants),
            ),
            _artifact("testdata/__init__.py", ArtifactKind.OTHER, ""),
            _artifact("testdata/loader.py", ArtifactKind.TEST_DATA, read_scaffold("testdata_loader.py")),
            _artifact(f"testdata/{names.data_file}", ArtifactKind.TEST_DATA, _data_file(test_case, names)),
            _artifact("config/__init__.py", ArtifactKind.OTHER, ""),
            _artifact("config/settings.py", ArtifactKind.CONFIG, read_scaffold("config_settings.py")),
            _artifact("config/navigation.py", ArtifactKind.CONFIG, read_scaffold("navigation_policy.py")),
            _artifact("conftest.py", ArtifactKind.CONFTEST, self._conftest()),
            _artifact(".gitignore", ArtifactKind.OTHER, _lines(["artifacts/", "testdata/*.local.json", "__pycache__/"])),
            _artifact("requirements.txt", ArtifactKind.REQUIREMENTS, _lines(self._templates["requirements"])),
            _artifact("pytest.ini", ArtifactKind.PYTEST_CONFIG, _lines(self._templates["pytestIni"])),
        ]

    def _check(self, test_case: CanonicalTestCase) -> list[LocatorResolution]:
        problems = []
        if self.target not in SUPPORTED_TARGETS[test_case.application_type]:
            problems.append(f"{self.target} does not support {test_case.application_type} applications")
        for step in test_case.steps:
            resolution = self._keyword_resolver.resolve_step(step, self.target, test_case.application_type)
            if not resolution.is_resolved:
                detail = resolution.message or "; ".join(issue.message for issue in resolution.issues)
                problems.append(f"Step {step.sequence}: {detail or resolution.status}")
        resolutions = self._locator_resolver.resolve_test_case(test_case)
        problems += [
            f"Steps {', '.join(map(str, r.step_sequences))}: {r.message}" for r in resolutions if not r.is_resolved
        ]
        problems += [f"Test data variable ${{{name}}} is not defined" for name in test_case.unresolved_variables()]
        if self._navigation is not None:
            problems += self._navigation.problems(test_case)
        if problems:
            raise GenerationError(test_case.test_case_id, problems)
        return resolutions

    def _test_module(
        self,
        test_case: CanonicalTestCase,
        names: ProjectNames,
        constants: dict[str, str],
        locators: dict[str, Locator],
    ) -> tuple[str, dict[int, int]]:
        fixture, fixture_type = self._templates["fixture"], self._templates["fixtureType"]
        imports = list(self._templates["baseImports"])
        for step in test_case.steps:
            imports += self._keyword_resolver.resolve(step.keyword, self.target).imports
        if constants:
            imports.append(f"from pages.{names.page_module} import {names.page_class}")
        imports.append(f"from testdata.loader import {DATA_CLASS}")

        source = test_case.source.system if test_case.source else "source"
        lines = [
            *_docstring(f"{test_case.name} ({test_case.test_case_id}): migrated from {source} by AutoMigrate AI."),
            "",
            *_organise_imports(imports),
            "",
            "log = logging.getLogger(__name__)",
            "",
            "",
            '@pytest.fixture(scope="module")',
            f"def {TEST_DATA_FIXTURE}() -> {DATA_CLASS}:",
            f"{INDENT}return {DATA_CLASS}.load({names.data_file!r})",
            "",
            "",
            f"def {names.test_function}({fixture}: {fixture_type}, {TEST_DATA_FIXTURE}: {DATA_CLASS}) -> None:",
            *_docstring(test_case.description or test_case.name, INDENT),
        ]
        step_line_map = {}
        for step in test_case.steps:
            ref = f"{names.page_class}.{constants[step.object_name]}" if step.object_name else None
            locator = locators[step.object_name] if step.object_name else None
            code = self._keyword_resolver.render(
                step, self.target, self._render_context(ref, locator), test_case.application_type
            )
            step_line_map[step.sequence] = len(lines) + 1
            lines.append(f"{INDENT}# {_printable(_marker(step))}")
            lines.append(f"{INDENT}log.info({_marker(step)!r})")
            lines += [f"{INDENT}{line}" for line in code.splitlines()]
        return _lines(lines), step_line_map

    def _page_module(
        self,
        test_case: CanonicalTestCase,
        names: ProjectNames,
        resolutions: list[LocatorResolution],
        constants: dict[str, str],
    ) -> str:
        lines = [*_docstring(f"Page object for {test_case.name}: locators from the source object repository."), ""]
        lines += ["", f"class {names.page_class}:"]
        if not resolutions:
            lines.append(f'{INDENT}"""This test case references no page objects."""')
        for resolution in resolutions:
            locator = self._locator_resolver.require(resolution)
            lines.append(f"{INDENT}# {_printable(f'{locator.name}: {locator.strategy}={locator.value}')}")
            lines.append(f"{INDENT}{constants[resolution.object_name]} = {self._locator_literal(locator)}")
        return _lines(lines)


def _artifact(path: str, kind: ArtifactKind, content: str) -> GeneratedArtifact:
    return GeneratedArtifact(path=path, kind=kind, content=content)


def read_scaffold(name: str) -> str:
    return (SCAFFOLD_DIR / name).read_text(encoding="utf-8")


def _lines(lines: Iterable[str]) -> str:
    return "\n".join(lines) + "\n"


def _data_file(test_case: CanonicalTestCase, names: ProjectNames) -> str:
    """Test data with every sensitive value left out (null); the values live in the local secrets file."""
    data = test_case.test_data
    sensitive = sorted(name for name in data.variables if data.is_sensitive(name))
    content: dict[str, Any] = {
        "testCaseId": test_case.test_case_id,
        "variables": {name: None if name in sensitive else value for name, value in data.variables.items()},
        "sensitiveKeys": sensitive,
    }
    if sensitive:
        content["secretsFile"] = names.secrets_file
    return _json(content)


def _secrets(test_case: CanonicalTestCase) -> dict[str, str]:
    data = test_case.test_data
    return {name: value for name, value in data.variables.items() if data.is_sensitive(name)}


def _json(content: dict[str, Any]) -> str:
    return json.dumps(content, indent=2, ensure_ascii=False) + "\n"


def _write(artifacts: list[GeneratedArtifact], output_dir: Path) -> None:
    root = output_dir.resolve()
    for artifact in artifacts:
        path = (root / artifact.path).resolve()
        if not path.is_relative_to(root):
            raise GenerationError(artifact.path, ["artifact path escapes the output directory"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(artifact.content, encoding="utf-8", newline="\n")


def _snake(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def _constant_names(object_names: Iterable[str]) -> dict[str, str]:
    constants: dict[str, str] = {}
    for object_name in object_names:
        base = _snake(object_name).upper() or "OBJECT"
        if base[0].isdigit():
            base = f"OBJ_{base}"
        name, suffix = base, 2
        while name in constants.values():
            name, suffix = f"{base}_{suffix}", suffix + 1
        constants[object_name] = name
    return constants


def _marker(step: TestStep) -> str:
    parts = [f"[AM-STEP {step.sequence}]", step.keyword]
    if step.object_name:
        parts.append(step.object_name)
    if step.block_id:
        parts.append(f"(block {step.block_id})")
    return " ".join(parts)


def _printable(text: str) -> str:
    """Escape characters that could end a comment or docstring line early."""
    return "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in text)


def _docstring(text: str, indent: str = "") -> list[str]:
    escaped = [_printable(line.replace("\\", "\\\\").replace('"', '\\"')).rstrip() for line in text.splitlines()]
    escaped = escaped or [""]
    while len(escaped) > 1 and not escaped[-1]:
        escaped.pop()
    if len(escaped) == 1:
        return [f'{indent}"""{escaped[0]}"""']
    return [f'{indent}"""{escaped[0]}', *[f"{indent}{line}" if line else "" for line in escaped[1:]], f'{indent}"""']


def _organise_imports(imports: Iterable[str]) -> list[str]:
    """Deduplicate, merge and group imports as stdlib / third-party / project, isort-style."""
    plain: dict[int, set[str]] = {0: set(), 1: set(), 2: set()}
    from_names: dict[tuple[int, str], set[str]] = {}
    for line in imports:
        if match := _FROM_IMPORT.fullmatch(line):
            module = match.group(1)
            names = {name.strip() for name in match.group(2).split(",")}
            from_names.setdefault((_import_group(module), module), set()).update(names)
        elif match := _PLAIN_IMPORT.fullmatch(line):
            plain[_import_group(match.group(1))].add(match.group(1))
        else:
            raise ValueError(f"unsupported import line in knowledge base: {line!r}")
    blocks = []
    for group in (0, 1, 2):
        block = [f"import {module}" for module in sorted(plain[group])]
        block += [
            f"from {module} import {', '.join(sorted(names))}"
            for (module_group, module), names in sorted(from_names.items())
            if module_group == group
        ]
        if block:
            blocks.append(block)
    return [line for i, block in enumerate(blocks) for line in ([""] if i else []) + block]


def _import_group(module: str) -> int:
    root = module.split(".")[0]
    if root in sys.stdlib_module_names:
        return 0
    return 2 if root in LOCAL_PACKAGES else 1
