"""Composition root: wires concrete implementations for FastAPI dependency injection."""

from functools import lru_cache

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver
from app.agents.migration_agent import MigrationAgent
from app.agents.migration_planner import MigrationPlanner
from app.agents.navigation_guard import NavigationGuard
from app.agents.repair_agent import RepairAgent
from app.agents.test_analyzer import TestAnalyzer
from app.agents.test_data_injector import TestDataInjector
from app.config import get_settings
from app.execution.engine import ExecutionEngine
from app.execution.playwright_runner import PlaywrightRunner
from app.execution.selenium_runner import SeleniumRunner
from app.generators.base_generator import BaseGenerator
from app.generators.playwright_generator import PlaywrightGenerator
from app.generators.selenium_generator import SeleniumGenerator
from app.knowledge.keyword_repository import KeywordRepository
from app.knowledge.loader import load_knowledge
from app.llm.base import LLMProvider
from app.llm.factory import create_llm_provider
from app.models.enums import TargetFramework
from app.parsers.base_parser import BaseParser
from app.parsers.icap_parser import IcapParser
from app.parsers.json_parser import JsonParser
from app.qtest.base_adapter import BaseQTestAdapter
from app.qtest.factory import create_qtest_adapter
from app.services.execution_service import ExecutionService
from app.services.migration_service import MigrationService
from app.services.qtest_service import QTestService
from app.services.repair_service import RepairService
from app.services.report_service import ReportService
from app.services.validation_service import ValidationService
from app.storage.json_store import JsonFileMigrationStore
from app.storage.migration_store import MigrationStore
from app.validation import ValidationEngine, create_validation_engine


@lru_cache
def get_store() -> MigrationStore:
    settings = get_settings()
    return JsonFileMigrationStore(settings.data_dir / "migrations", settings.generated_dir)


@lru_cache
def get_llm_provider() -> LLMProvider:
    return create_llm_provider(get_settings())


@lru_cache
def get_keyword_repository() -> KeywordRepository:
    return KeywordRepository.load()


@lru_cache
def get_parser() -> BaseParser:
    return IcapParser(JsonParser(get_keyword_repository(), max_expanded_steps=get_settings().max_expanded_steps))


@lru_cache
def get_navigation_guard() -> NavigationGuard:
    return NavigationGuard.from_prefixes(get_settings().allowed_target_urls, get_keyword_repository())


@lru_cache
def get_generators() -> dict[TargetFramework, BaseGenerator]:
    keyword_resolver = KeywordResolver(get_keyword_repository())
    locator_resolver = LocatorResolver(load_knowledge("locator_rules"))
    templates = load_knowledge("framework_templates")
    navigation = get_navigation_guard()
    return {
        TargetFramework.PLAYWRIGHT: PlaywrightGenerator(keyword_resolver, locator_resolver, templates, navigation),
        TargetFramework.SELENIUM: SeleniumGenerator(keyword_resolver, locator_resolver, templates, navigation),
    }


@lru_cache
def get_migration_agent() -> MigrationAgent:
    return MigrationAgent(
        analyzer=TestAnalyzer(get_llm_provider()),
        planner=MigrationPlanner(
            KeywordResolver(get_keyword_repository()),
            LocatorResolver(load_knowledge("locator_rules")),
            TestDataInjector(),
            get_navigation_guard(),
        ),
        generators=get_generators(),
    )


@lru_cache
def get_execution_engine() -> ExecutionEngine:
    settings = get_settings()
    args = (settings.generated_dir, settings.execution_timeout_seconds, settings.headless)
    allowed = settings.allowed_target_urls
    return ExecutionEngine(
        {
            TargetFramework.PLAYWRIGHT: PlaywrightRunner(*args, allowed_urls=allowed),
            TargetFramework.SELENIUM: SeleniumRunner(*args, allowed_urls=allowed),
        },
        max_concurrent=settings.max_concurrent_executions,
    )


@lru_cache
def get_qtest_adapter() -> BaseQTestAdapter:
    return create_qtest_adapter(get_settings())


@lru_cache
def get_migration_service() -> MigrationService:
    return MigrationService(
        get_store(),
        get_parser(),
        get_migration_agent(),
        get_settings().generated_dir,
        KeywordResolver(get_keyword_repository()),
    )


@lru_cache
def get_validation_engine() -> ValidationEngine:
    return create_validation_engine(
        KeywordResolver(get_keyword_repository()),
        LocatorResolver(load_knowledge("locator_rules")),
        load_knowledge("coding_standards"),
        load_knowledge("framework_templates"),
    )


@lru_cache
def get_validation_service() -> ValidationService:
    return ValidationService(get_store(), get_validation_engine())


@lru_cache
def get_execution_service() -> ExecutionService:
    return ExecutionService(get_store(), get_execution_engine())


@lru_cache
def get_repair_service() -> RepairService:
    return RepairService(
        get_store(),
        RepairAgent(get_llm_provider(), get_generators()),
        get_generators(),
        get_validation_engine(),
        get_execution_engine(),
    )


@lru_cache
def get_qtest_service() -> QTestService:
    return QTestService(get_store(), get_qtest_adapter())


@lru_cache
def get_report_service() -> ReportService:
    return ReportService(get_store(), LocatorResolver(load_knowledge("locator_rules")))
