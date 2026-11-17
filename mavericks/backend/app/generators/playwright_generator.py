from app.agents.keyword_resolver import RenderContext
from app.generators.base_generator import TEST_DATA_FIXTURE, BaseGenerator, read_scaffold
from app.models.canonical_test import Locator
from app.models.enums import TargetFramework


class PlaywrightGenerator(BaseGenerator):
    target = TargetFramework.PLAYWRIGHT

    def _locator_literal(self, locator: Locator) -> str:
        return repr(self._locator_resolver.to_playwright(locator).selector)

    def _render_context(self, locator_ref: str | None, locator: Locator | None) -> RenderContext:
        return RenderContext(locator_ref=locator_ref, data_resolver=f"{TEST_DATA_FIXTURE}.resolve")

    def _conftest(self) -> str:
        return read_scaffold("playwright_conftest.py")
