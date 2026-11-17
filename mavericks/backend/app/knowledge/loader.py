import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, get_args

KNOWLEDGE_DIR = Path(__file__).resolve().parent

KnowledgeFile = Literal["keyword_kb", "locator_rules", "framework_templates", "coding_standards"]


@lru_cache
def load_knowledge(name: KnowledgeFile) -> dict[str, Any]:
    if name not in get_args(KnowledgeFile):
        raise ValueError(f"Unknown knowledge file: {name!r}")
    return json.loads((KNOWLEDGE_DIR / f"{name}.json").read_text(encoding="utf-8"))
