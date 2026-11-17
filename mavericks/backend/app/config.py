from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Default navigation allowlist: only the bundled demo app. Add your application's URLs via ALLOWED_TARGET_URLS.
DEFAULT_ALLOWED_TARGET_URLS = ["http://localhost:8001/demo/", "http://127.0.0.1:8001/demo/"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_name: str = "AutoMigrate AI"
    app_version: str = "0.1.0"
    environment: Literal["local", "dev", "demo", "prod"] = "local"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Shared API key for every /api route except /api/health. Optional only for ENVIRONMENT=local.
    api_key: SecretStr | None = None
    # Host headers the backend answers to (DNS-rebinding protection). Ports are ignored.
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "[::1]"]
    # URL prefixes generated tests may navigate to; everything else is rejected at plan, generate and run time.
    allowed_target_urls: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_TARGET_URLS))

    llm_provider: Literal["mock", "azure_openai"] = "mock"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    llm_timeout_seconds: float = Field(default=30, gt=0)

    qtest_mode: Literal["mock", "real"] = "mock"
    qtest_base_url: str | None = None
    qtest_api_token: SecretStr | None = None
    qtest_project_id: str | None = None

    generated_dir: Path = PROJECT_ROOT / "generated"
    data_dir: Path = PROJECT_ROOT / "data"
    execution_timeout_seconds: int = Field(default=120, gt=0, le=3600)
    # Generated test runs (pytest + browser) allowed at the same time; further requests get HTTP 429.
    max_concurrent_executions: int = Field(default=2, ge=1, le=32)
    headless: bool = True
    max_upload_bytes: int = Field(default=1_048_576, gt=0, le=20_000_000)
    # Upper bound on steps after reusable blocks are expanded (stops N**depth "block bombs").
    max_expanded_steps: int = Field(default=500, ge=1, le=10_000)

    @model_validator(mode="after")
    def _require_api_key_outside_local(self) -> Self:
        if self.environment != "local" and (self.api_key is None or not self.api_key.get_secret_value().strip()):
            raise ValueError(f"API_KEY must be set when ENVIRONMENT={self.environment}; only 'local' may run without it")
        if self.api_key is not None and len(self.api_key.get_secret_value()) < 16:
            raise ValueError("API_KEY must be at least 16 characters long")
        return self

    @property
    def docs_enabled(self) -> bool:
        return self.environment == "local"

    @property
    def demo_app_enabled(self) -> bool:
        return self.environment in ("local", "demo")


@lru_cache
def get_settings() -> Settings:
    return Settings()
