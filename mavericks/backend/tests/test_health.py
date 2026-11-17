from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["appName"] == "AutoMigrate AI"
    assert body["llmProvider"] in {"mock", "azure_openai"}
    assert body["qtestMode"] in {"mock", "real"}
