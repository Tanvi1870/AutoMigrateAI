import json
import urllib.error
import urllib.request
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.llm.azure_openai_provider import AzureOpenAIProvider
from app.llm.base import TASK_KEY, LLMError, LLMProvider
from app.llm.factory import PROVIDERS, create_llm_provider
from app.llm.mock_provider import MockLLMProvider
from app.models import AITestAnalysis


class _Answer(BaseModel):
    answer: str


class _FixedProvider(LLMProvider):
    name = "fixed"

    def __init__(self, output: str) -> None:
        self._output = output

    def generate(self, prompt: str, context: dict[str, Any]) -> str:
        return self._output


def test_factory_defaults_to_mock() -> None:
    assert isinstance(create_llm_provider(Settings(llm_provider="mock")), MockLLMProvider)


def test_factory_requires_azure_configuration() -> None:
    settings = Settings(
        llm_provider="azure_openai", azure_openai_endpoint=None, azure_openai_api_key=None, azure_openai_deployment=None
    )

    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        create_llm_provider(settings)


def test_generate_json_validates_schema() -> None:
    assert _FixedProvider('{"answer": "42"}').generate_json("q", {}, _Answer).answer == "42"


def test_generate_json_rejects_invalid_output() -> None:
    with pytest.raises(LLMError):
        _FixedProvider("not json").generate_json("q", {}, _Answer)


def test_provider_is_selected_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "5")
    provider = create_llm_provider(Settings())
    assert isinstance(provider, AzureOpenAIProvider)
    assert provider._timeout == 5


def test_new_providers_plug_in_through_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PROVIDERS, "mock", lambda settings: _FixedProvider("{}"))
    assert isinstance(create_llm_provider(Settings(llm_provider="mock")), _FixedProvider)


class TestMockProvider:
    def test_is_deterministic_and_schema_valid(self) -> None:
        context = {TASK_KEY: "test_analysis", "testCase": {"name": "T", "steps": []}, "analysis": {}}
        first = MockLLMProvider().generate_json("p", context, AITestAnalysis)
        assert first == MockLLMProvider().generate_json("p", context, AITestAnalysis)
        assert first.risks  # no assertion step

    def test_unknown_task_is_an_llm_error(self) -> None:
        with pytest.raises(LLMError, match="no rule"):
            MockLLMProvider().generate("p", {TASK_KEY: "write_code"})


class _Response:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestAzureOpenAIProvider:
    def _provider(self) -> AzureOpenAIProvider:
        return AzureOpenAIProvider(
            "https://example.openai.azure.com/", SecretStr("secret-key"), "gpt 4o", "2024-10-21", timeout_seconds=7
        )

    def test_requires_https_endpoint(self) -> None:
        with pytest.raises(ValueError, match="https"):
            AzureOpenAIProvider("http://example.com", SecretStr("k"), "d", "v")

    def test_sends_json_mode_chat_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
            captured.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
            captured["body"] = json.loads(request.data)
            return _Response({"choices": [{"message": {"content": '{"answer": "42"}'}}]})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert self._provider().generate_json("system prompt", {"x": 1}, _Answer).answer == "42"
        assert captured["url"] == (
            "https://example.openai.azure.com/openai/deployments/gpt%204o/chat/completions?api-version=2024-10-21"
        )
        assert captured["headers"]["Api-key"] == "secret-key" and captured["timeout"] == 7
        assert captured["body"]["response_format"] == {"type": "json_object"}
        assert captured["body"]["messages"] == [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": '{"x": 1}'},
        ]

    @pytest.mark.parametrize(
        "failure",
        [
            urllib.error.HTTPError("u", 401, "Unauthorized", {}, None),  # type: ignore[arg-type]
            urllib.error.URLError("no route"),
            TimeoutError(),
        ],
    )
    def test_transport_failures_are_llm_errors_without_secrets(
        self, failure: Exception, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*args: object, **kwargs: object) -> None:
            raise failure

        monkeypatch.setattr(urllib.request, "urlopen", fail)
        with pytest.raises(LLMError) as excinfo:
            self._provider().generate("p", {})
        assert "secret-key" not in str(excinfo.value)

    @pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}])
    def test_malformed_responses_are_llm_errors(self, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(body))
        with pytest.raises(LLMError):
            self._provider().generate("p", {})
