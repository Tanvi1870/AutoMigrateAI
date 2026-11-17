import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urlparse

from pydantic import SecretStr

from app.llm.base import LLMError, LLMProvider


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI chat completions in JSON mode, over plain HTTPS so no SDK is required."""

    name = "azure_openai"

    def __init__(
        self, endpoint: str, api_key: SecretStr, deployment: str, api_version: str, timeout_seconds: float = 30
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("AZURE_OPENAI_ENDPOINT must be an https:// URL")
        self._url = (
            f"https://{parsed.netloc}/openai/deployments/{quote(deployment, safe='')}"
            f"/chat/completions?api-version={quote(api_version, safe='')}"
        )
        self._api_key = api_key
        self._timeout = timeout_seconds

    def generate(self, prompt: str, context: dict[str, Any]) -> str:
        body = {
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "api-key": self._api_key.get_secret_value()},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            raise LLMError(f"{self.name} request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"{self.name} request failed: {type(exc).__name__}") from None
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name} returned an unexpected response: {type(exc).__name__}") from None
        if not isinstance(content, str):
            raise LLMError(f"{self.name} returned no message content")
        return content
