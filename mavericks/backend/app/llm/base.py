from abc import ABC, abstractmethod
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Every request names its task in context[TASK_KEY] so providers (and the offline mock) can tell requests apart.
TASK_KEY = "task"


class LLMError(RuntimeError):
    """Raised when the LLM fails or returns output that does not match the expected schema."""


class LLMProvider(ABC):
    """Provider-neutral interface; implementations must raise LLMError for every failure."""

    name: ClassVar[str]

    @abstractmethod
    def generate(self, prompt: str, context: dict[str, Any]) -> str:
        """Return the raw model completion (a JSON document) for the prompt and structured context."""

    def generate_json(self, prompt: str, context: dict[str, Any], schema: type[T]) -> T:
        raw = self.generate(prompt, context)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMError(f"{self.name} returned output that does not match {schema.__name__}") from exc
