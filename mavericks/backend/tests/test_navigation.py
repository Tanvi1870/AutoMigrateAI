"""Navigation allowlist (C-02) and sensitive data in URLs (H-02)."""

import json
from typing import Any

import pytest

from app.agents.navigation_guard import NavigationGuard
from app.config import PROJECT_ROOT
from app.dependencies import get_keyword_repository, get_parser
from app.generators.base_generator import GenerationError
from app.generators.playwright_generator import PlaywrightGenerator
from app.generators.scaffold.navigation_policy import NavigationNotAllowedError, NavigationPolicy, normalise
from app.models import CanonicalTestCase
from tests.test_validation import KEYWORDS, LOCATORS, TEMPLATES

DEMO = ["http://localhost:8001/demo/"]
POLICY = NavigationPolicy(DEMO)
GUARD = NavigationGuard(POLICY, get_keyword_repository())
SAMPLE = PROJECT_ROOT / "samples" / "icap" / "login_test.json"


def sample(**test_data: str) -> CanonicalTestCase:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["testData"].update(test_data)
    return get_parser().parse(json.dumps(data))


def with_navigate(value: str, **test_data: str) -> CanonicalTestCase:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["steps"][0]["value"] = value
    data["testData"].update(test_data)
    return get_parser().parse(json.dumps(data))


class TestPolicy:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8001/demo/login",
            "http://localhost:8001/demo",
            "HTTP://LOCALHOST:8001/demo/v2/login?x=1#top",
            "http://localhost:8001/demo/./login",
            "http://localhost:8001//demo//login",
        ],
    )
    def test_allowed(self, url: str) -> None:
        assert POLICY.allows_navigation(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://localhost:8001/api/migration/MIG-001/report",  # the backend's own API
            "http://localhost:8001/demo/../api/health",  # dot segments
            "http://localhost:8001/demo/%2e%2e/api/health",  # encoded dot segments
            "http://localhost:8001/demo/..%2fapi",
            "http://localhost:8001/demonstration/",  # prefix must end at a path segment
            "http://localhost:8001@evil.example/demo/",  # credentials trick
            "http://evil.example/demo/",
            "https://localhost:8001/demo/",  # different scheme
            "http://localhost:8002/demo/",  # different port
            "http://127.0.0.1:8001/demo/",  # different host spelling is not implied
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://localhost:8001\\@evil.example/demo/",
            "http://localhost:8001/demo/ login",
            "",
        ],
    )
    def test_blocked(self, url: str) -> None:
        assert not POLICY.allows_navigation(url)
        with pytest.raises(NavigationNotAllowedError):
            POLICY.check_navigation(url)

    def test_subresources_only_need_an_allowed_origin(self) -> None:
        assert POLICY.allows_request("http://localhost:8001/static/app.js")
        assert POLICY.allows_request("data:image/png;base64,AAAA")
        assert not POLICY.allows_request("http://cdn.evil.example/app.js")
        assert not POLICY.allows_request("http://169.254.169.254/")

    def test_invalid_entries_are_rejected(self) -> None:
        with pytest.raises(NavigationNotAllowedError):
            NavigationPolicy(["ftp://files.example/"])

    def test_environment_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AM_ALLOWED_URLS", POLICY.to_environment())
        assert NavigationPolicy.from_environment().allowed_prefixes == DEMO
        monkeypatch.delenv("AM_ALLOWED_URLS")
        assert NavigationPolicy.from_environment() is None

    def test_default_ports_are_normalised(self) -> None:
        assert normalise("https://App.Example.com/a/")[:3] == normalise("https://app.example.com:443/b")[:3]


class TestGuard:
    def test_sample_is_allowed(self) -> None:
        assert GUARD.problems(sample()) == []

    def test_test_data_pointing_elsewhere_is_reported(self) -> None:
        problems = GUARD.problems(sample(BASE_URL="http://169.254.169.254/latest"))
        assert len(problems) == 1 and "is not an allowed navigation target" in problems[0] and "Step 1" in problems[0]

    def test_sensitive_data_in_a_url_is_reported(self) -> None:
        problems = GUARD.problems(with_navigate("${BASE_URL}/login?p=${PASSWORD}"))
        assert problems == ["Step 1: sensitive test data (${PASSWORD}) must not be used in a URL"]

    def test_generator_refuses_disallowed_targets(self) -> None:
        generator = PlaywrightGenerator(KEYWORDS, LOCATORS, TEMPLATES, GUARD)
        with pytest.raises(GenerationError, match="not an allowed navigation target"):
            generator.build(sample(BASE_URL="http://internal.corp/admin"))
        assert generator.build(sample())  # the sample itself still generates


class TestApi:
    """The real app wiring blocks the plan and generation for a disallowed URL."""

    def _upload(self, client: Any, **test_data: str) -> str:
        data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        data["testData"].update(test_data)
        response = client.post("/api/migration/upload", files={"file": ("t.json", json.dumps(data).encode())})
        assert response.status_code == 201, response.text
        return response.json()["migrationId"]

    def test_plan_is_blocked_and_generation_refused(self, client: Any) -> None:
        migration_id = self._upload(client, BASE_URL="http://169.254.169.254/latest")
        plan = client.post("/api/migration/analyze", json={"migrationId": migration_id}).json()
        assert plan["status"] == "BLOCKED"
        assert any("not an allowed navigation target" in reason for reason in plan["blockingReasons"])
        response = client.post("/api/migration/generate", json={"migrationId": migration_id})
        assert response.status_code == 409 and response.json()["code"] == "MIGRATION_BLOCKED"
