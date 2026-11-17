"""API exposure controls: API key, trusted hosts, docs and demo-app gating, fail-closed configuration."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import create_app

KEY = "k" * 24
HOSTS = ["localhost", "testserver"]


def secured(**overrides: Any) -> Settings:
    return Settings(api_key=KEY, allowed_hosts=HOSTS, **overrides)


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = secured()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client


def assert_unauthorized(response: Any) -> None:
    body = response.json()
    assert response.status_code == 401, body
    assert body["code"] == "UNAUTHORIZED" and "X-API-Key" in body["message"]


class TestApiKey:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/migration/upload"),
            ("post", "/api/migration/analyze"),
            ("post", "/api/migration/execute"),
            ("post", "/api/migration/repair"),
            ("get", "/api/migration/MIG-001/report"),
            ("post", "/api/qtest/sync"),
            ("get", "/api/qtest/MIG-001/traceability"),
        ],
    )
    def test_every_api_route_requires_the_key(self, client: TestClient, method: str, path: str) -> None:
        assert_unauthorized(getattr(client, method)(path))

    def test_wrong_key_is_rejected(self, client: TestClient) -> None:
        assert_unauthorized(client.post("/api/migration/upload", headers={"X-API-Key": "wrong-key-of-some-length"}))

    def test_correct_key_reaches_the_route(self, client: TestClient) -> None:
        response = client.post("/api/migration/upload", headers={"X-API-Key": KEY})
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"  # missing file, not 401

    def test_health_stays_open_and_reports_auth(self, client: TestClient) -> None:
        body = client.get("/api/health").json()
        assert body["status"] == "ok" and body["authRequired"] is True


class TestFailClosedConfiguration:
    @pytest.mark.parametrize("environment", ["dev", "demo", "prod"])
    def test_non_local_environments_need_an_api_key(self, environment: str) -> None:
        with pytest.raises(ValidationError, match="API_KEY must be set"):
            Settings(environment=environment, api_key=None)

    def test_short_keys_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 16 characters"):
            Settings(api_key="short")

    def test_local_without_key_is_allowed(self) -> None:
        assert Settings(environment="local", api_key=None).api_key is None


class TestExposure:
    def test_untrusted_host_header_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/health", headers={"Host": "attacker.example"})
        assert response.status_code == 400

    @pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/redoc"])
    def test_docs_are_only_served_locally(self, path: str) -> None:
        with TestClient(create_app(secured(environment="demo"))) as client:
            assert client.get(path).status_code == 404
        with TestClient(create_app(secured(environment="local"))) as client:
            assert client.get(path).status_code == 200

    def test_demo_app_is_not_mounted_in_prod(self) -> None:
        with TestClient(create_app(secured(environment="prod"))) as client:
            assert client.get("/demo/login").status_code == 404
        with TestClient(create_app(secured(environment="demo"))) as client:
            assert client.get("/demo/login").status_code == 200

    def test_cors_allows_the_api_key_header(self, client: TestClient) -> None:
        response = client.options(
            "/api/migration/upload",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert response.status_code == 200
        assert "x-api-key" in response.headers["access-control-allow-headers"].lower()
