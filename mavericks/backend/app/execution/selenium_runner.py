from app.execution.runner import BaseRunner
from app.models.enums import TargetFramework


class SeleniumRunner(BaseRunner):
    """Selenium via the generated conftest's `driver` fixture; Selenium Manager provides the driver."""

    target = TargetFramework.SELENIUM

    def browser_args(self) -> list[str]:
        return [] if self._headless else ["--selenium-headed"]
