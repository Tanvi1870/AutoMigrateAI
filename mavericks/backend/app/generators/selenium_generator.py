from app.agents.keyword_resolver import RenderContext
from app.generators.base_generator import TEST_DATA_FIXTURE, BaseGenerator, read_scaffold
from app.models.canonical_test import Locator
from app.models.enums import TargetFramework


class SeleniumGenerator(BaseGenerator):
    """Page constants hold the locator value; the By strategy is rendered at each step by the KB template."""

    target = TargetFramework.SELENIUM

    def _locator_literal(self, locator: Locator) -> str:
        return repr(self._locator_resolver.to_selenium(locator).value)

    def _render_context(self, locator_ref: str | None, locator: Locator | None) -> RenderContext:
        by = self._locator_resolver.to_selenium(locator).by if locator is not None else None
        return RenderContext(by=by, locator_value_ref=locator_ref, data_resolver=f"{TEST_DATA_FIXTURE}.resolve")

    def _conftest(self) -> str:
        return read_scaffold("selenium_conftest.py")
