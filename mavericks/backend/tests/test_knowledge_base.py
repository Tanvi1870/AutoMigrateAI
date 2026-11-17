import pytest

from app.knowledge.loader import load_knowledge
from app.models.capabilities import LOCATOR_STRATEGIES, SUPPORTED_TARGETS
from app.models.enums import ApplicationType, TargetFramework

MVP_KEYWORDS = {"web_navigate", "web_click", "web_setText", "web_getText", "web_assertText", "web_wait", "web_select"}


@pytest.mark.parametrize("name", ["keyword_kb", "locator_rules", "framework_templates", "coding_standards"])
def test_knowledge_files_load(name: str) -> None:
    assert load_knowledge(name)["version"]


def test_unknown_knowledge_file_is_rejected() -> None:
    with pytest.raises(ValueError):
        load_knowledge("../secrets")  # type: ignore[arg-type]


def test_keyword_kb_covers_mvp_keywords_for_all_targets() -> None:
    keywords = load_knowledge("keyword_kb")["keywords"]

    assert MVP_KEYWORDS <= keywords.keys()
    for keyword in MVP_KEYWORDS:
        for target in TargetFramework:
            assert keywords[keyword]["targets"][target]["python"], f"{keyword} missing {target} template"


def test_windows_keywords_are_future_adapters_only() -> None:
    kb = load_knowledge("keyword_kb")

    assert not any(k.startswith("win_") for k in kb["keywords"])
    for adapter in kb["futureAdapters"].values():
        assert adapter["status"] == "FUTURE_ADAPTER"
        assert "targets" not in adapter
        assert SUPPORTED_TARGETS[ApplicationType(adapter["applicationType"])] == frozenset()


def test_locator_rules_cover_every_web_strategy_and_target() -> None:
    strategies = load_knowledge("locator_rules")["strategies"]

    assert set(strategies) == LOCATOR_STRATEGIES[ApplicationType.WEB]
    for strategy in LOCATOR_STRATEGIES[ApplicationType.WEB]:
        assert strategies[strategy]["playwright"]["selector"]
        assert strategies[strategy]["selenium"]["by"]


def test_framework_templates_cover_every_target() -> None:
    targets = load_knowledge("framework_templates")["targets"]

    assert set(TargetFramework) <= targets.keys()
