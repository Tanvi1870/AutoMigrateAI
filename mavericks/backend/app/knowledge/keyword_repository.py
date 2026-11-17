import difflib
from dataclasses import dataclass
from typing import Any, Self

from pydantic import ValidationError

from app.knowledge.keyword_definitions import (
    FutureAdapterDefinition,
    KeywordDefinition,
    KeywordKnowledgeBase,
    KeywordStatus,
)
from app.knowledge.loader import load_knowledge
from app.models.enums import ApplicationType

__all__ = ["KeywordInfo", "KeywordRepository", "KeywordStatus", "KnowledgeBaseError"]


class KnowledgeBaseError(ValueError):
    """keyword_kb.json does not satisfy the knowledge-base schema."""


@dataclass(frozen=True)
class KeywordInfo:
    keyword: str
    status: KeywordStatus
    application_type: ApplicationType | None = None
    requires: tuple[str, ...] = ()
    note: str | None = None


class KeywordRepository:
    """Read-only, validated access to the keyword knowledge base."""

    def __init__(self, knowledge_base: KeywordKnowledgeBase) -> None:
        self._kb = knowledge_base
        # Longest prefix first so 'win_TE_' wins over 'win_'.
        self._future_prefixes = sorted(knowledge_base.future_adapters.items(), key=lambda i: len(i[0]), reverse=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            return cls(KeywordKnowledgeBase.model_validate(data))
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg'].removeprefix('Value error, ')}" for e in exc.errors()
            )
            raise KnowledgeBaseError(f"Invalid keyword knowledge base: {details}") from None

    @classmethod
    def load(cls) -> Self:
        return cls.from_dict(load_knowledge("keyword_kb"))

    @property
    def version(self) -> str:
        return self._kb.version

    @property
    def keywords(self) -> tuple[str, ...]:
        return tuple(self._kb.keywords)

    def get(self, keyword: str) -> KeywordDefinition | None:
        return self._kb.keywords.get(keyword)

    def future_adapter(self, keyword: str) -> tuple[str, FutureAdapterDefinition] | None:
        return next(((p, a) for p, a in self._future_prefixes if keyword.startswith(p)), None)

    def classify(self, keyword: str) -> KeywordInfo:
        definition = self.get(keyword)
        if definition is not None:
            return KeywordInfo(
                keyword=keyword,
                status=definition.status,
                application_type=definition.application_type,
                requires=tuple(str(p) for p in definition.required),
                note=definition.assumption,
            )
        match = self.future_adapter(keyword)
        if match is not None:
            _, adapter = match
            return KeywordInfo(keyword, KeywordStatus.FUTURE_ADAPTER, adapter.application_type, note=adapter.note)
        return KeywordInfo(keyword, KeywordStatus.UNKNOWN)

    def suggest(self, keyword: str, limit: int = 3) -> list[str]:
        by_lower = {name.lower(): name for name in self._kb.keywords}
        matches = difflib.get_close_matches(keyword.lower(), by_lower, n=limit, cutoff=0.75)
        return [by_lower[match] for match in matches]
