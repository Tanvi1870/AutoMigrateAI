from app.execution.runner import BaseRunner
from app.models.enums import TargetFramework


class PlaywrightRunner(BaseRunner):
    """pytest-playwright; the browser comes from the project's pytest.ini (`--browser chromium`)."""

    target = TargetFramework.PLAYWRIGHT

    def browser_args(self) -> list[str]:
        return [] if self._headless else ["--headed"]
