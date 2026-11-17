"""Sensitive test data stays out of artifacts, API responses and LLM prompts (H-01)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT
from app.dependencies import get_parser
from app.generators.base_generator import SCAFFOLD_DIR
from app.generators.playwright_generator import PlaywrightGenerator
from app.models.canonical_test import is_sensitive_name
from tests.test_api import build_app
from tests.test_repair import FakeBrowser
from tests.test_validation import KEYWORDS, LOCATORS, TEMPLATES

SAMPLE = PROJECT_ROOT / "samples" / "icap" / "login_test.json"
SECRET = "demo_password"


def sample(**test_data: str):  # noqa: ANN201
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["testData"].update(test_data)
    return get_parser().parse(json.dumps(data))


def load_dataset(project: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("am_testdata_loader", project / "testdata" / "loader.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["am_testdata_loader"] = module
    spec.loader.exec_module(module)
    return module


class TestGeneratedProject:
    def test_artifacts_never_contain_sensitive_values(self) -> None:
        artifacts = PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES).build(sample())
        assert not [a.path for a in artifacts if SECRET in a.content]
        data = json.loads(next(a.content for a in artifacts if a.path == "testdata/login_data.json"))
        assert data["variables"]["PASSWORD"] is None and data["variables"]["USERNAME"] == "demo_user"
        assert data["secretsFile"] == "login_secrets.local.json" and data["sensitiveKeys"] == ["PASSWORD"]

    def test_values_are_written_to_the_local_secrets_file_only(self, tmp_path: Path) -> None:
        generator = PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES)
        artifacts = generator.generate(sample(), tmp_path)
        secrets_file = tmp_path / "testdata" / "login_secrets.local.json"
        assert json.loads(secrets_file.read_text(encoding="utf-8"))["secrets"] == {"PASSWORD": SECRET}
        assert "testdata/login_secrets.local.json" not in {a.path for a in artifacts}
        assert "testdata/*.local.json" in (tmp_path / ".gitignore").read_text(encoding="utf-8")

    def test_secrets_file_is_removed_when_nothing_is_sensitive(self, tmp_path: Path) -> None:
        generator = PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES)
        generator.generate(sample(), tmp_path)
        test_case = sample()
        data = test_case.test_data.model_copy(update={"variables": {"BASE_URL": "http://localhost:8001/demo", "USERNAME": "u", "PASS_HINT": "x"}})
        # A test case whose data has no sensitive names: the old secrets file must not linger.
        plain = test_case.model_copy(update={"test_data": data, "steps": test_case.steps[:2]})
        generator.write_secrets(plain, tmp_path)
        assert not (tmp_path / "testdata" / "login_secrets.local.json").exists()


class TestRuntimeLoader:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES).generate(sample(), tmp_path)
        return tmp_path

    def test_reads_secrets_from_the_local_file(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AM_DATA_PASSWORD", raising=False)
        data = load_dataset(project).DataSet.load("login_data.json")
        assert data.get("PASSWORD") == SECRET and data.resolve("${USERNAME}") == "demo_user"

    def test_environment_override_wins(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AM_DATA_PASSWORD", "from-env")
        assert load_dataset(project).DataSet.load("login_data.json").get("PASSWORD") == "from-env"

    def test_missing_secret_fails_with_guidance(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AM_DATA_PASSWORD", raising=False)
        (project / "testdata" / "login_secrets.local.json").unlink()
        data = load_dataset(project).DataSet.load("login_data.json")
        with pytest.raises(KeyError, match="set the AM_DATA_PASSWORD environment variable"):
            data.get("PASSWORD")

    def test_loader_scaffold_is_the_generated_loader(self, project: Path) -> None:
        generated = (project / "testdata" / "loader.py").read_text(encoding="utf-8")
        assert generated == (SCAFFOLD_DIR / "testdata_loader.py").read_text(encoding="utf-8")


class TestApiResponses:
    def test_generate_and_report_never_return_the_value(self, tmp_path: Path) -> None:
        with TestClient(build_app(tmp_path / "generated", FakeBrowser())) as client:
            migration_id = client.post(
                "/api/migration/upload", files={"file": ("login.json", SAMPLE.read_bytes())}
            ).json()["migrationId"]
            ref = {"migrationId": migration_id}
            bodies = [
                client.post("/api/migration/analyze", json=ref).text,
                client.post("/api/migration/generate", json=ref).text,
                client.post("/api/migration/validate", json=ref).text,
                client.get(f"/api/migration/{migration_id}/report").text,
            ]
        assert all(SECRET not in body for body in bodies)


@pytest.mark.parametrize(
    ("name", "sensitive"),
    [
        ("PASSWORD", True),
        ("password", True),
        ("userPassword", True),
        ("apiToken", True),
        ("API_KEY", True),
        ("clientSecret", True),
        ("user_pin", True),
        ("userPin", True),
        ("USERNAME", False),
        ("loginButton", False),
        ("spinner", False),
        ("BASE_URL", False),
    ],
)
def test_sensitive_names(name: str, sensitive: bool) -> None:
    assert is_sensitive_name(name) is sensitive
