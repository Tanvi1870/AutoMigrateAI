"""The validators run by the ValidationEngine. Each one owns specific checks and reports clear, located issues."""

import ast
import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

from pyflakes.checker import Checker
from pyflakes.messages import ImportStarUsed, UndefinedName, UnusedImport

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver
from app.models.capabilities import SUPPORTED_TARGETS
from app.models.enums import Severity, TargetFramework, ValidationCheck
from app.models.fields import VARIABLE_REFERENCE
from app.models.keyword_resolution import ResolutionStatus
from app.models.validation_result import ValidationIssue
from app.validation.engine import ValidationContext, Validator, issue

ERROR, WARNING = Severity.ERROR, Severity.WARNING
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class SyntaxValidator(Validator):
    checks = (ValidationCheck.SYNTAX,)

    def validate(self, context: ValidationContext) -> list[ValidationIssue]:
        return self.check_sources(context.files)

    @staticmethod
    def check_sources(files: Mapping[str, str]) -> list[ValidationIssue]:
        issues = []
        for path, source in files.items():
            if path.endswith(".py"):
                try:
                    compile(source, path, "exec", dont_inherit=True)  # also catches compile-time errors ast misses
                except SyntaxError as exc:
                    line = exc.lineno if exc.lineno and exc.lineno > 0 else None
                    issues.append(issue(ValidationCheck.SYNTAX, ERROR, f"Invalid Python: {exc.msg}", file=path, line=line))
            elif path.endswith(".json"):
                try:
                    json.loads(source)
                except ValueError as exc:
                    issues.append(issue(ValidationCheck.SYNTAX, ERROR, f"Invalid JSON: {exc}", file=path))
        return issues


class ImportValidator(Validator):
    """Allow-listed imports, project modules that exist, no undefined names, and the right framework throughout."""

    checks = (ValidationCheck.IMPORTS, ValidationCheck.FRAMEWORK)

    def __init__(self, coding_standards: dict[str, Any], framework_templates: dict[str, Any]) -> None:
        roots = coding_standards["allowedImportRoots"]
        self._common = set(roots["common"])
        self._project = set(roots["project"])
        self._framework_roots = {target: set(roots[target]) for target in TargetFramework}
        self._templates = framework_templates["targets"]
        self._prefix = coding_standards["testFunctionPrefix"]

    def validate(self, context: ValidationContext) -> list[ValidationIssue]:
        issues = []
        for path, tree in context.trees.items():
            issues += self._imports(context, path, tree)
            issues += _pyflakes(path, tree)
        return issues + self._framework(context)

    def _imports(self, context: ValidationContext, path: str, tree: ast.Module) -> list[ValidationIssue]:
        issues = []
        allowed = self._common | self._project | self._framework_roots[context.target]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [(alias.name, []) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level or node.module is None:
                    issues.append(_at(ValidationCheck.IMPORTS, ERROR, "Relative imports are not allowed", path, node))
                    continue
                imports = [(node.module, [alias.name for alias in node.names])]
            else:
                continue
            for module, names in imports:
                root = module.split(".")[0]
                owner = self._framework_owner(root, context.target)
                if owner is not None:
                    message = f"Imports '{module}' from {owner} in a {context.target} project"
                    issues.append(_at(ValidationCheck.FRAMEWORK, ERROR, message, path, node))
                elif root not in allowed:
                    message = f"Import '{module}' is not on the allow-list for {context.target} projects"
                    issues.append(_at(ValidationCheck.IMPORTS, ERROR, message, path, node))
                elif root in self._project:
                    issues += _project_import(context, path, node, module, names)
        return issues

    def _framework_owner(self, root: str, target: TargetFramework) -> TargetFramework | None:
        if root in self._framework_roots[target]:
            return None
        return next((other for other, roots in self._framework_roots.items() if root in roots), None)

    def _framework(self, context: ValidationContext) -> list[ValidationIssue]:
        check, target = ValidationCheck.FRAMEWORK, context.target
        issues = []
        if target not in SUPPORTED_TARGETS[context.test_case.application_type]:
            message = f"{target} cannot run {context.test_case.application_type} tests"
            issues.append(issue(check, ERROR, message))
        for required_file in ("conftest.py", "pytest.ini", "requirements.txt"):
            if required_file not in context.files:
                issues.append(issue(check, ERROR, f"{required_file} is missing from the project"))

        if "requirements.txt" in context.files:
            present = _requirement_names(context.files["requirements.txt"])
            required = _requirement_names("\n".join(self._templates[target]["requirements"]))
            issues += [
                issue(check, ERROR, f"requirements.txt does not include {name}", file="requirements.txt")
                for name in sorted(required - present)
            ]
            for other in TargetFramework:
                foreign = _requirement_names("\n".join(self._templates[other]["requirements"])) - required
                issues += [
                    issue(check, WARNING, f"requirements.txt includes {name}, which belongs to {other}", file="requirements.txt")
                    for name in sorted(foreign & present)
                ]

        fixture = self._templates[target]["fixture"]
        foreign_fixtures = {self._templates[t]["fixture"]: t for t in TargetFramework if t != target}
        for path, tree in context.test_trees.items():
            for function in _test_functions(tree, self._prefix):
                params = {arg.arg for arg in function.args.args}
                for param in sorted(params & foreign_fixtures.keys()):
                    message = f"Test '{function.name}' uses the '{param}' fixture from {foreign_fixtures[param]}"
                    issues.append(_at(check, ERROR, message, path, function))
                if fixture not in params:
                    message = f"Test '{function.name}' does not use the {target} '{fixture}' fixture"
                    issues.append(_at(check, ERROR, message, path, function))
        return issues


class KeywordValidator(Validator):
    """Every source step maps to a supported keyword, has generated code, and its ${VAR} data resolves."""

    checks = (ValidationCheck.KEYWORDS, ValidationCheck.VARIABLES)

    def __init__(self, keyword_resolver: KeywordResolver) -> None:
        self._resolver = keyword_resolver

    def validate(self, context: ValidationContext) -> list[ValidationIssue]:
        return self._keywords(context) + self._variables(context)

    def _keywords(self, context: ValidationContext) -> list[ValidationIssue]:
        check, test_case = ValidationCheck.KEYWORDS, context.test_case
        issues, assumed = [], set()
        for step in test_case.steps:
            resolution = self._resolver.resolve_step(step, context.target, test_case.application_type)
            if resolution.status is ResolutionStatus.UNSUPPORTED:
                hint = f" (did you mean {', '.join(resolution.suggestions)}?)" if resolution.suggestions else ""
                issues.append(issue(check, ERROR, f"{resolution.message}{hint}", step=step.sequence))
            elif resolution.status is ResolutionStatus.INVALID:
                issues += [issue(check, ERROR, i.message, step=step.sequence) for i in resolution.issues]
            else:
                if resolution.assumption and step.keyword not in assumed:
                    assumed.add(step.keyword)
                    message = f"{step.keyword} semantics are assumed: {resolution.assumption}"
                    issues.append(issue(check, WARNING, message, step=step.sequence))
                issues += [issue(check, WARNING, w, step=step.sequence) for w in resolution.warnings]

        if context.test_files:
            markers = {path: context.step_markers(path) for path in context.test_files}
            generated = {sequence for found in markers.values() for sequence, _ in found}
            source = {step.sequence for step in test_case.steps}
            issues += [
                issue(check, ERROR, "Step has no generated code (no [AM-STEP] marker in the tests)", step=sequence)
                for sequence in sorted(source - generated)
            ]
            for path, found in markers.items():
                issues += [
                    issue(check, ERROR, f"Code for step {sequence}, which is not in the source test", file=path, line=line)
                    for sequence, line in found
                    if sequence not in source
                ]
        return issues

    def _variables(self, context: ValidationContext) -> list[ValidationIssue]:
        check, test_case = ValidationCheck.VARIABLES, context.test_case
        issues = []
        unresolved = test_case.unresolved_variables()
        for name in unresolved:
            first = next(s.sequence for s in test_case.steps if name in s.variable_references)
            issues.append(issue(check, ERROR, f"${{{name}}} has no value in the test data", step=first))

        referenced = set(test_case.referenced_variables()) - set(unresolved)
        data_files = [p for p in context.files if p.startswith("testdata/") and p.endswith(".json")]
        if referenced and not data_files:
            issues.append(issue(check, ERROR, "No test data file found under testdata/"))
        elif referenced:
            available = set().union(*(_data_variables(context.files[p]) for p in data_files))
            issues += [
                issue(check, ERROR, f"${{{name}}} is missing from the generated test data files")
                for name in sorted(referenced - available)
            ]

        for path, tree in context.trees.items():
            resolved, docstrings = _resolved_arguments(tree), _docstrings(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and VARIABLE_REFERENCE.search(node.value)
                    and id(node) not in resolved | docstrings
                ):
                    message = f"Placeholder {node.value!r} is used literally and never resolved from the test data"
                    issues.append(_at(check, ERROR, message, path, node))

        data = test_case.test_data
        secrets = {name: value for name, value in data.variables.items() if data.is_sensitive(name) and len(value) >= 4}
        for path, source in context.files.items():
            if path.endswith(".py"):
                issues += [
                    issue(check, WARNING, f"The value of sensitive variable {name} is hard-coded", file=path)
                    for name, value in secrets.items()
                    if value in source
                ]
        return issues


class LocatorValidator(Validator):
    """Every referenced object has a usable locator, and the tests only use page-object constants that exist."""

    checks = (ValidationCheck.LOCATORS,)

    def __init__(self, locator_resolver: LocatorResolver) -> None:
        self._resolver = locator_resolver

    def validate(self, context: ValidationContext) -> list[ValidationIssue]:
        check = ValidationCheck.LOCATORS
        issues = []
        for resolution in self._resolver.resolve_test_case(context.test_case):
            step = resolution.step_sequences[0] if resolution.step_sequences else None
            if not resolution.is_resolved:
                hint = f" (did you mean {', '.join(resolution.suggestions)}?)" if resolution.suggestions else ""
                issues.append(issue(check, ERROR, f"{resolution.message}{hint}", step=step))
            issues += [issue(check, WARNING, warning, step=step) for warning in resolution.warnings]

        page_constants = {
            node.name: _class_attributes(node)
            for path, tree in context.trees.items()
            if path.startswith("pages/")
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        for path, tree in context.test_trees.items():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in page_constants
                    and node.attr not in page_constants[node.value.id]
                ):
                    message = f"Locator {node.value.id}.{node.attr} is not defined in the page object"
                    issues.append(_at(check, ERROR, message, path, node))
        return issues


class StandardsValidator(Validator):
    """Coding standards, assertions and duplicate steps."""

    checks = (ValidationCheck.ASSERTIONS, ValidationCheck.DUPLICATES, ValidationCheck.STANDARDS)

    def __init__(self, coding_standards: dict[str, Any]) -> None:
        self._forbidden = list(coding_standards["forbiddenCalls"])
        self._require_assertion = bool(coding_standards["requireAssertion"])
        self._prefix = coding_standards["testFunctionPrefix"]
        self._max_line = int(coding_standards["maxLineLength"])

    def validate(self, context: ValidationContext) -> list[ValidationIssue]:
        issues = self._duplicate_source_steps(context)
        if not context.test_files:
            message = "No test module found under tests/ (expected test_*.py or *_test.py)"
            issues.append(issue(ValidationCheck.STANDARDS, ERROR, message))
        for path in context.test_files:
            issues += self._test_module(context, path)
        for path, tree in context.trees.items():
            issues += self._calls(path, tree)
        for path, source in context.files.items():
            if path.endswith(".py"):
                long_lines = [n for n, line in enumerate(source.splitlines(), start=1) if len(line) > self._max_line]
                if long_lines:
                    message = f"{len(long_lines)} line(s) longer than {self._max_line} characters"
                    issues.append(issue(ValidationCheck.STANDARDS, WARNING, message, file=path, line=long_lines[0]))
        return issues

    def _test_module(self, context: ValidationContext, path: str) -> list[ValidationIssue]:
        issues = []
        seen: set[int] = set()
        for sequence, line in context.step_markers(path):
            if sequence in seen:
                message = f"Step {sequence} is generated more than once"
                issues.append(issue(ValidationCheck.DUPLICATES, ERROR, message, file=path, line=line, step=sequence))
            seen.add(sequence)

        tree = context.trees.get(path)
        if tree is None:
            return issues  # syntax error, already reported
        functions = _test_functions(tree, self._prefix)
        if not functions:
            message = f"No test functions (names must start with '{self._prefix}')"
            issues.append(issue(ValidationCheck.STANDARDS, ERROR, message, file=path))
        for name, count in Counter(f.name for f in functions).items():
            if count > 1:
                message = f"Test '{name}' is defined {count} times; only the last definition runs"
                issues.append(issue(ValidationCheck.DUPLICATES, ERROR, message, file=path))
        for function in functions:
            if not _has_assertion(function):
                severity = ERROR if self._require_assertion else WARNING
                message = f"Test '{function.name}' has no assertion, so it can pass without checking any outcome"
                issues.append(_at(ValidationCheck.ASSERTIONS, severity, message, path, function))
        return issues

    def _calls(self, path: str, tree: ast.Module) -> list[ValidationIssue]:
        issues = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or (name := _dotted_name(node.func)) is None:
                continue
            if any(name == forbidden or name.startswith(f"{forbidden}.") for forbidden in self._forbidden):
                issues.append(_at(ValidationCheck.STANDARDS, ERROR, f"Forbidden call '{name}'", path, node))
            elif name == "time.sleep":
                message = "Fixed sleep makes tests slow and flaky; use an explicit wait"
                issues.append(_at(ValidationCheck.STANDARDS, WARNING, message, path, node))
        return issues

    @staticmethod
    def _duplicate_source_steps(context: ValidationContext) -> list[ValidationIssue]:
        steps = context.test_case.steps
        return [
            issue(
                ValidationCheck.DUPLICATES,
                WARNING,
                f"Repeats step {previous.sequence} ({step.keyword}) exactly; check for a copy-paste error",
                step=step.sequence,
            )
            for previous, step in zip(steps, steps[1:])
            if (previous.keyword, previous.object_name, previous.value, previous.expected)
            == (step.keyword, step.object_name, step.value, step.expected)
        ]


def _at(check: ValidationCheck, severity: Severity, message: str, path: str, node: ast.AST) -> ValidationIssue:
    return issue(check, severity, message, file=path, line=getattr(node, "lineno", None))


def _pyflakes(path: str, tree: ast.Module) -> list[ValidationIssue]:
    severities = {UndefinedName: ERROR, ImportStarUsed: ERROR, UnusedImport: WARNING}
    return [
        issue(ValidationCheck.IMPORTS, severities[type(m)], m.message % m.message_args, file=path, line=m.lineno)
        for m in Checker(tree, filename=path).messages
        if type(m) in severities
    ]


def _project_import(
    context: ValidationContext, path: str, node: ast.AST, module: str, names: list[str]
) -> list[ValidationIssue]:
    module_path = _module_file(context.files, module)
    if module_path is None:
        return [_at(ValidationCheck.IMPORTS, ERROR, f"Module '{module}' is not part of the project", path, node)]
    tree = context.trees.get(module_path)
    if tree is None:
        return []  # syntax error in the imported module, already reported
    defined = _top_level_names(tree)
    return [
        _at(ValidationCheck.IMPORTS, ERROR, f"'{name}' is not defined in {module}", path, node)
        for name in names
        if name != "*" and name not in defined and _module_file(context.files, f"{module}.{name}") is None
    ]


def _module_file(files: Mapping[str, str], module: str) -> str | None:
    base = module.replace(".", "/")
    return next((p for p in (f"{base}.py", f"{base}/__init__.py") if p in files), None)


def _top_level_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
    return names


def _class_attributes(node: ast.ClassDef) -> set[str]:
    return _top_level_names(ast.Module(body=node.body, type_ignores=[]))


def _test_functions(tree: ast.Module, prefix: str) -> list[ast.FunctionDef]:
    """Functions pytest collects as tests: prefixed and not a fixture (e.g. the generated `test_data` fixture)."""
    return [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith(prefix) and not any(map(_is_fixture, n.decorator_list))
    ]


def _is_fixture(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (_dotted_name(target) or "").split(".")[-1] == "fixture"


def _has_assertion(function: ast.FunctionDef) -> bool:
    """A plain assert, or a Playwright `expect(...).to_*()` call."""
    return any(
        isinstance(node, ast.Assert)
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr.startswith("to_"))
        for node in ast.walk(function)
    )


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted_name(node.value)) is not None:
        return f"{base}.{node.attr}"
    return None


def _resolved_arguments(tree: ast.Module) -> set[int]:
    """Nodes passed to a `*.resolve(...)` call, i.e. placeholders resolved from the test data at runtime."""
    return {
        id(arg)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "resolve"
        for arg in node.args
    }


def _docstrings(tree: ast.Module) -> set[int]:
    owners = [tree, *(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef))]
    return {
        id(owner.body[0].value)
        for owner in owners
        if owner.body and isinstance(owner.body[0], ast.Expr) and isinstance(owner.body[0].value, ast.Constant)
    }


def _requirement_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        if match := _REQUIREMENT_NAME.match(line):
            names.add(match.group(1).lower().replace("_", "-"))
    return names


def _data_variables(source: str) -> set[str]:
    try:
        content = json.loads(source)
    except ValueError:
        return set()  # reported by SyntaxValidator
    variables = content.get("variables") if isinstance(content, dict) else None
    return set(variables) if isinstance(variables, dict) else set()
