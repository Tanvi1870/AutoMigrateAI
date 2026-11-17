from collections.abc import Callable

from app.config import Settings
from app.llm.azure_openai_provider import AzureOpenAIProvider
from app.llm.base import LLMProvider
from app.llm.mock_provider import MockLLMProvider


def _mock(settings: Settings) -> LLMProvider:
    return MockLLMProvider()


def _azure_openai(settings: Settings) -> LLMProvider:
    required = {
        "AZURE_OPENAI_ENDPOINT": settings.azure_openai_endpoint,
        "AZURE_OPENAI_API_KEY": settings.azure_openai_api_key,
        "AZURE_OPENAI_DEPLOYMENT": settings.azure_openai_deployment,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"LLM_PROVIDER=azure_openai requires: {', '.join(missing)}")
    return AzureOpenAIProvider(
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        deployment=settings.azure_openai_deployment,
        api_version=settings.azure_openai_api_version,
        timeout_seconds=settings.llm_timeout_seconds,
    )


# LLM_PROVIDER selects an entry; a new provider only needs an LLMProvider subclass and a line here.
PROVIDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "mock": _mock,
    "azure_openai": _azure_openai,
}


def create_llm_provider(settings: Settings) -> LLMProvider:
    try:
        builder = PROVIDERS[settings.llm_provider]
    except KeyError:
        raise ValueError(f"Unknown LLM_PROVIDER {settings.llm_provider!r}; expected one of {sorted(PROVIDERS)}") from None
    return builder(settings)
