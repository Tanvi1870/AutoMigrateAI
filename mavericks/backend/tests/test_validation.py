import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver
from app.dependencies import get_keyword_repository, get_validation_service
from app.generators.playwright_generator import PlaywrightGenerator
from app.generators.selenium_generator import SeleniumGenerator
from app.knowledge.loader import load_knowledge
from app.main import create_app
from app.models import (
    CanonicalTestCase,
    CheckStatus,
    MigrationRecord,
    MigrationResult,
    Severity,
    TargetFramework,
    ValidationCheck,
    ValidationIssue,
)
from app.services.validation_service import MigrationStateError, ValidationService
from app.storage.migration_store import InMemoryMigrationStore, MigrationNotFoundError
from app.validation import ValidationEngine, Validator, create_validation_engine
from app.validation.engine import ValidationContext, read_project
from tests.factories import NOW, login_payload, make_test_case

KEYWORDS = KeywordResolver(get_keyword_repository())
LOCATORS = LocatorResolver(load_knowledge("locator_rules"))
TEMPLATES = load_knowledge("framework_templates")
STANDARDS = load_knowledge("coding_standards")
ENGINE = create_validation_engine(KEYWORDS, LOCATORS, STANDARDS, TEMPLATES)
GENERATORS = {
    TargetFramework.PLAYWRIGHT: PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES),
    TargetFramework.SELENIUM: SeleniumGenerator(KEYWORDS, LOCATORS, TEMPLATES),
}
PW, SE = TargetFramework.PLAYWRIGHT, TargetFramework.SELENIUM
TEST_FILE = "tests/login_test.py"


def project(test_case: CanonicalTestCase | None = None, target: TargetFramework = PW) -> dict[str, str]:
    return {a.path: a.content for a in GENERATORS[target].build(test_case or make_test_case())}


def edit(files: dict[str, str], path: str, change: Callable[[str], str]) -> dict[str, str]:
    edited = change(files[path])
    assert edited != files[path], "the edit did not change the file"
    return files | {path: edited}


def validate(files: dict[str, str], test_case: CanonicalTestCase | None = None, target: TargetFramework = PW):
    return ENGINE.validate_files(files, test_case or make_test_case(), target)


def reasons(result: Any, check: ValidationCheck) -> list[str]:
    return next(summary.reasons for summary in result.checks if summary.check is check)


def with_step(**fields: Any) -> list[dict[str, Any]]:
    return login_payload()["steps"] + [{"sequence": 6, **fields}]


class TestCleanProjects:
    @pytest.mark.parametrize("target", list(TargetFramework))
    def test_generated_login_project_passes_every_check(self, target: TargetFramework, tmp_path: Path) -> None:
        GENERATORS[target].generate(make_test_case(), tmp_path)
        result = ENGINE.validate(tmp_path, make_test_case(), target)
        assert result.status is CheckStatus.PASS, result.summary
        assert result.target_framework is target and result.issues == []
        assert all(result.check_status(check) is CheckStatus.PASS for check in ValidationCheck)
        assert result.summary == "PASS: all checks passed"

    def test_ui_shape_lists_every_check_with_label_status_and_reasons(self) -> None:
        dumped = validate(project()).model_dump(mode="json", by_alias=True)
        assert dumped["status"] == "PASS"
        assert [c["label"] for c in dumped["checks"]] == [
            "Python syntax",
            "Imports",
            "Keywords",
            "Locators",
            "Test data variables",
            "Assertions",
            "Duplicate steps",
            "Framework compatibility",
            "Coding standards",
        ]
        assert all(c["status"] == "PASS" and c["reasons"] == [] for c in dumped["checks"])

    def test_warning_is_serialised_as_warning(self) -> None:
        test_case = make_test_case(steps=with_step(keyword="web_wait", object="loginButton"))
        dumped = validate(project(test_case), test_case).model_dump(mode="json", by_alias=True)
        assert dumped["status"] == "WARNING"
        assert dumped["keywords"] == "WARNING"
        assert dumped["summary"] == "WARNING: 1 warning"


class TestSyntaxValidator:
    def test_syntax_error_is_located(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s.replace("def test_login(", "def test_login(("))
        result = validate(files)
        assert result.syntax is CheckStatus.FAIL and result.status is CheckStatus.FAIL
        assert reasons(result, ValidationCheck.SYNTAX)[0].startswith(f"{TEST_FILE}:")
        assert result.keywords is CheckStatus.PASS  # other checks still run

    def test_compile_time_errors_are_caught(self) -> None:
        files = project() | {"pages/extra.py": "return 1\n"}
        assert "'return' outside function" in reasons(validate(files), ValidationCheck.SYNTAX)[0]

    def test_invalid_json_test_data(self) -> None:
        files = project() | {"testdata/login_data.json": "{not json"}
        result = validate(files)
        assert result.syntax is CheckStatus.FAIL
        assert "Invalid JSON" in reasons(result, ValidationCheck.SYNTAX)[0]


class TestImportValidator:
    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (lambda s: "import requests\n" + s, "Import 'requests' is not on the allow-list for playwright projects"),
            (lambda s: s.replace("import LoginPage", "import LoginPage, Missing"), "'Missing' is not defined in pages.login_page"),
            (lambda s: s.replace("pages.login_page", "pages.nope"), "Module 'pages.nope' is not part of the project"),
            (lambda s: s.replace("from testdata.loader", "from .loader"), "Relative imports are not allowed"),
        ],
        ids=["not-allowed", "missing-name", "missing-module", "relative"],
    )
    def test_import_errors(self, change: Callable[[str], str], reason: str) -> None:
        result = validate(edit(project(), TEST_FILE, change))
        assert result.imports is CheckStatus.FAIL
        assert any(reason in r for r in reasons(result, ValidationCheck.IMPORTS)), reasons(result, ValidationCheck.IMPORTS)

    def test_undefined_name_fails(self) -> None:
        files = edit(project(target=SE), TEST_FILE, lambda s: s.replace("from selenium.webdriver.common.by import By\n", ""))
        result = validate(files, target=SE)
        assert result.imports is CheckStatus.FAIL
        assert any("undefined name 'By'" in r for r in reasons(result, ValidationCheck.IMPORTS))

    def test_unused_import_warns(self) -> None:
        result = validate(edit(project(), TEST_FILE, lambda s: "import json\n" + s))
        assert result.imports is CheckStatus.WARN and result.status is CheckStatus.WARN
        assert "'json' imported but unused" in reasons(result, ValidationCheck.IMPORTS)[0]


class TestFrameworkCompatibility:
    def test_other_framework_import_fails(self) -> None:
        files = edit(project(target=SE), TEST_FILE, lambda s: "from playwright.sync_api import Page\n" + s)
        result = validate(files, target=SE)
        assert result.framework is CheckStatus.FAIL
        assert "Imports 'playwright.sync_api' from playwright in a selenium project" in reasons(
            result, ValidationCheck.FRAMEWORK
        )[0]

    def test_playwright_project_validated_as_selenium_fails(self) -> None:
        result = validate(project(target=PW), target=SE)
        framework = " | ".join(reasons(result, ValidationCheck.FRAMEWORK))
        assert result.framework is CheckStatus.FAIL
        assert "uses the 'page' fixture from playwright" in framework
        assert "does not use the selenium 'driver' fixture" in framework
        assert "requirements.txt does not include selenium" in framework

    def test_foreign_requirement_warns(self) -> None:
        files = edit(project(target=SE), "requirements.txt", lambda s: s + "playwright>=1.45\n")
        result = validate(files, target=SE)
        assert result.framework is CheckStatus.WARN
        assert "includes playwright, which belongs to playwright" in reasons(result, ValidationCheck.FRAMEWORK)[0]

    @pytest.mark.parametrize("missing", ["conftest.py", "pytest.ini", "requirements.txt"])
    def test_missing_project_files(self, missing: str) -> None:
        files = {path: text for path, text in project().items() if path != missing}
        assert f"{missing} is missing from the project" in reasons(validate(files), ValidationCheck.FRAMEWORK)

    def test_target_must_support_the_application_type(self) -> None:
        windows = make_test_case(
            applicationType="WINDOWS",
            steps=[{"sequence": 1, "keyword": "win_click", "object": "okButton"}],
            testData={},
            reusableBlocks=[],
            objectRepository={"okButton": {"name": "okButton", "strategy": "automation-id", "value": "ok"}},
        )
        result = validate(project(), windows)
        assert "playwright cannot run WINDOWS tests" in reasons(result, ValidationCheck.FRAMEWORK)


class TestKeywordValidator:
    def test_unsupported_keyword_in_source(self) -> None:
        source = make_test_case(steps=with_step(keyword="web_hover", object="loginButton"))
        result = validate(project(), source)
        keyword_reasons = reasons(result, ValidationCheck.KEYWORDS)
        assert result.keywords is CheckStatus.FAIL
        assert any(r.startswith("Step 6: Keyword 'web_hover' is not in the keyword knowledge base") for r in keyword_reasons)
        assert "Step 6: Step has no generated code (no [AM-STEP] marker in the tests)" in keyword_reasons

    def test_invalid_parameters(self) -> None:
        source = make_test_case(steps=with_step(keyword="web_wait", object="loginButton", value="soon"))
        assert any("web_wait timeout" in r for r in reasons(validate(project(), source), ValidationCheck.KEYWORDS))

    def test_assumed_semantics_warn(self) -> None:
        source = make_test_case(steps=with_step(keyword="web_wait", object="loginButton"))
        result = validate(project(source), source)
        assert result.keywords is CheckStatus.WARN
        assert reasons(result, ValidationCheck.KEYWORDS)[0].startswith("Step 6: web_wait semantics are assumed")

    def test_missing_step_code(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s.replace("# [AM-STEP 4]", "# step 4"))
        assert "Step 4: Step has no generated code (no [AM-STEP] marker in the tests)" in reasons(
            validate(files), ValidationCheck.KEYWORDS
        )

    def test_code_for_unknown_step(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s + "    # [AM-STEP 99] web_click\n")
        assert any("Code for step 99" in r for r in reasons(validate(files), ValidationCheck.KEYWORDS))


class TestVariables:
    def test_unresolved_variable_in_source(self) -> None:
        steps = login_payload()["steps"]
        steps[1]["value"] = "${MISSING}"
        result = validate(project(), make_test_case(steps=steps))
        assert result.variables is CheckStatus.FAIL
        assert "Step 2: ${MISSING} has no value in the test data" in reasons(result, ValidationCheck.VARIABLES)

    def test_variable_missing_from_generated_data(self) -> None:
        data = json.loads(project()["testdata/login_data.json"])
        del data["variables"]["USERNAME"]
        files = project() | {"testdata/login_data.json": json.dumps(data)}
        assert "${USERNAME} is missing from the generated test data files" in reasons(
            validate(files), ValidationCheck.VARIABLES
        )

    def test_literal_placeholder_fails(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s.replace("test_data.resolve('${USERNAME}')", "'${USERNAME}'"))
        result = validate(files)
        assert result.variables is CheckStatus.FAIL
        assert "Placeholder '${USERNAME}' is used literally" in reasons(result, ValidationCheck.VARIABLES)[0]

    def test_hard_coded_secret_warns_without_revealing_it(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s.replace("test_data.resolve('${PASSWORD}')", "'demo_password'"))
        result = validate(files)
        assert result.variables is CheckStatus.WARN
        assert reasons(result, ValidationCheck.VARIABLES) == [
            f"{TEST_FILE}: The value of sensitive variable PASSWORD is hard-coded"
        ]
        assert "demo_password" not in json.dumps(result.model_dump(mode="json"))


class TestLocatorValidator:
    def test_missing_locator_in_source(self) -> None:
        repository = login_payload()["objectRepository"]
        del repository["loginButton"]
        result = validate(project(), make_test_case(objectRepository=repository))
        assert result.locators is CheckStatus.FAIL
        assert reasons(result, ValidationCheck.LOCATORS) == [
            "Step 4: Object 'loginButton' is not defined in the object repository"
        ]

    def test_test_uses_undefined_page_constant(self) -> None:
        files = edit(project(), "pages/login_page.py", lambda s: s.replace("    LOGIN_BUTTON = ", "    LOGIN_BTN = "))
        result = validate(files)
        assert result.locators is CheckStatus.FAIL
        assert "Locator LoginPage.LOGIN_BUTTON is not defined in the page object" in reasons(
            result, ValidationCheck.LOCATORS
        )[0]

    def test_ai_inferred_locator_warns(self) -> None:
        repository = login_payload()["objectRepository"]
        repository["loginButton"] |= {"source": "AI_INFERRED", "confidence": 0.7}
        source = make_test_case(objectRepository=repository)
        result = validate(project(source), source)
        assert result.locators is CheckStatus.WARN
        assert "AI_INFERRED (confidence 0.70)" in reasons(result, ValidationCheck.LOCATORS)[0]


class TestStandardsValidator:
    def test_missing_assertion_fails(self) -> None:
        source = make_test_case(steps=login_payload()["steps"][:4])
        result = validate(project(source), source)
        assert result.assertions is CheckStatus.FAIL
        assert "Test 'test_login' has no assertion" in reasons(result, ValidationCheck.ASSERTIONS)[0]

    def test_missing_assertion_only_warns_when_not_required(self) -> None:
        engine = create_validation_engine(KEYWORDS, LOCATORS, STANDARDS | {"requireAssertion": False}, TEMPLATES)
        source = make_test_case(steps=login_payload()["steps"][:4])
        result = engine.validate_files(project(source), source, PW)
        assert result.assertions is CheckStatus.WARN and result.passed

    def test_consecutive_duplicate_source_steps_warn(self) -> None:
        steps = login_payload()["steps"]
        steps.insert(4, steps[3] | {"sequence": 5})
        steps[5]["sequence"] = 6
        source = make_test_case(steps=steps)
        result = validate(project(source), source)
        assert result.duplicates is CheckStatus.WARN
        assert reasons(result, ValidationCheck.DUPLICATES) == [
            "Step 5: Repeats step 4 (web_click) exactly; check for a copy-paste error"
        ]

    def test_duplicated_generated_step_fails(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s + "    # [AM-STEP 4] web_click loginButton\n")
        result = validate(files)
        assert result.duplicates is CheckStatus.FAIL
        assert "Step 4 is generated more than once" in reasons(result, ValidationCheck.DUPLICATES)[0]

    def test_duplicate_test_function_fails(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s + "\n\ndef test_login(page: Page) -> None:\n    assert True\n")
        assert any("defined 2 times" in r for r in reasons(validate(files), ValidationCheck.DUPLICATES))

    @pytest.mark.parametrize(
        ("code", "status", "reason"),
        [
            ("eval('1')", CheckStatus.FAIL, "Forbidden call 'eval'"),
            ("os.system('dir')", CheckStatus.FAIL, "Forbidden call 'os.system'"),
            ("time.sleep(1)", CheckStatus.WARN, "Fixed sleep"),
            ("x = '" + "a" * 130 + "'", CheckStatus.WARN, "1 line(s) longer than 120 characters"),
        ],
    )
    def test_code_standards(self, code: str, status: CheckStatus, reason: str) -> None:
        files = project() | {"pages/helpers.py": f"import os\nimport time\n\n{code}\n"}
        result = validate(files)
        assert result.standards is status
        assert reason in reasons(result, ValidationCheck.STANDARDS)[0]

    def test_no_test_module(self) -> None:
        files = {path: text for path, text in project().items() if path != TEST_FILE}
        result = validate(files)
        assert "No test module found under tests/" in reasons(result, ValidationCheck.STANDARDS)[0]

    def test_fixtures_are_not_tests(self) -> None:
        files = edit(project(), TEST_FILE, lambda s: s.replace("def test_login(", "def login("))
        result = validate(files)
        assert reasons(result, ValidationCheck.STANDARDS) == [
            f"{TEST_FILE}: No test functions (names must start with 'test_')"
        ]


class TestEngine:
    def test_validator_cannot_report_checks_it_does_not_own(self) -> None:
        class Rogue(Validator):
            checks = (ValidationCheck.SYNTAX,)

            def validate(self, context: ValidationContext) -> list[ValidationIssue]:
                return [ValidationIssue(check=ValidationCheck.LOCATORS, severity=Severity.ERROR, message="x")]

        with pytest.raises(ValueError, match="does not own"):
            ValidationEngine([Rogue()]).validate_files(project(), make_test_case(), PW)

    def test_checks_without_a_validator_are_skipped(self) -> None:
        result = ValidationEngine([]).validate_files(project(), make_test_case(), PW)
        assert result.status is CheckStatus.SKIPPED and result.summary == "SKIPPED: no checks were run"

    def test_read_project_skips_run_output(self, tmp_path: Path) -> None:
        GENERATORS[PW].generate(make_test_case(), tmp_path)
        for junk in ("artifacts/test-run.json", "tests/__pycache__/x.py", ".pytest_cache/v.json"):
            (tmp_path / junk).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / junk).write_text("{", encoding="utf-8")
        files = read_project(tmp_path)
        assert TEST_FILE in files and not any(p.startswith(("artifacts/", ".pytest")) or "__pycache__" in p for p in files)

    def test_missing_project_dir(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ENGINE.validate(tmp_path / "nope", make_test_case(), PW)

    def test_issue_reason_formats(self) -> None:
        def reason(**fields: Any) -> str:
            return ValidationIssue(check=ValidationCheck.SYNTAX, severity=Severity.ERROR, message="m", **fields).reason

        assert reason() == "m"
        assert reason(step_sequence=3) == "Step 3: m"
        assert reason(file="a.py") == "a.py: m"
        assert reason(file="a.py", line=2, step_sequence=3) == "a.py:2 (step 3): m"


def seeded_store(tmp_path: Path, target: TargetFramework | None = PW) -> InMemoryMigrationStore:
    store = InMemoryMigrationStore()
    test_case = make_test_case()
    results = {}
    if target is not None:
        artifacts = GENERATORS[target].generate(test_case, tmp_path)
        results[target] = MigrationResult(
            migration_id="MIG-001",
            test_case_id=test_case.test_case_id,
            target_framework=target,
            output_dir=tmp_path.as_posix(),
            artifacts=artifacts,
            generated_at=NOW,
        )
    store.save(MigrationRecord(migration_id="MIG-001", created_at=NOW, test_case=test_case, results=results))
    return store


class TestValidationService:
    def test_validates_and_stores_the_result(self, tmp_path: Path) -> None:
        store = seeded_store(tmp_path)
        result = ValidationService(store, ENGINE).validate("MIG-001", PW)
        assert result.status is CheckStatus.PASS
        assert store.get("MIG-001").validations[PW] == result

    def test_requires_a_generated_project(self, tmp_path: Path) -> None:
        with pytest.raises(MigrationStateError, match="generate it first"):
            ValidationService(seeded_store(tmp_path), ENGINE).validate("MIG-001", SE)

    def test_missing_project_folder(self, tmp_path: Path) -> None:
        store = seeded_store(tmp_path / "gone")
        for path in sorted((tmp_path / "gone").rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (tmp_path / "gone").rmdir()
        with pytest.raises(MigrationStateError, match="missing"):
            ValidationService(store, ENGINE).validate("MIG-001", PW)

    def test_unknown_migration(self, tmp_path: Path) -> None:
        with pytest.raises(MigrationNotFoundError):
            ValidationService(InMemoryMigrationStore(), ENGINE).validate("MIG-404", PW)


class TestValidateEndpoint:
    def _client(self, store: InMemoryMigrationStore) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_validation_service] = lambda: ValidationService(store, ENGINE)
        return TestClient(app)

    def test_returns_ui_ready_result(self, tmp_path: Path) -> None:
        with self._client(seeded_store(tmp_path)) as client:
            response = client.post("/api/migration/validate", json={"migrationId": "MIG-001", "targetFramework": "playwright"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PASS" and body["targetFramework"] == "playwright"
        assert {c["check"] for c in body["checks"]} == {check.value for check in ValidationCheck}

    def test_not_generated_is_a_conflict(self, tmp_path: Path) -> None:
        with self._client(seeded_store(tmp_path, target=None)) as client:
            response = client.post("/api/migration/validate", json={"migrationId": "MIG-001"})
        assert response.status_code == 409 and "generate it first" in response.json()["message"]

    def test_unknown_migration_is_not_found(self) -> None:
        with self._client(InMemoryMigrationStore()) as client:
            response = client.post("/api/migration/validate", json={"migrationId": "MIG-404"})
        assert response.status_code == 404
