"""WebDriver setup, logging, failure screenshots and teardown for the generated Selenium tests.

Pick the browser with `--selenium-browser chrome|firefox|edge` and show it with `--selenium-headed`.
Selenium Manager downloads a matching driver when none is on PATH.
AM_BROWSER_ARGS adds browser flags, e.g. "--no-sandbox --disable-dev-shm-usage" inside containers.
"""

import logging
import os
import re
from collections.abc import Iterator

import pytest
from selenium import webdriver
from selenium.webdriver.remote.webdriver import WebDriver

from config import settings
from config.navigation import NavigationNotAllowedError, NavigationPolicy

log = logging.getLogger("automigrate")
_REPORTS = pytest.StashKey[dict[str, pytest.TestReport]]()
_BROWSERS = ("chrome", "firefox", "edge")
# Set by AutoMigrate AI for every run; None when the project is run by hand outside the backend.
_NAVIGATION = NavigationPolicy.from_environment()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("automigrate-selenium")
    group.addoption("--selenium-browser", choices=_BROWSERS, default="chrome", help="browser to run the tests in")
    group.addoption("--selenium-headed", action="store_true", default=False, help="show the browser window")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[pytest.TestReport]:
    report = yield
    item.stash.setdefault(_REPORTS, {})[report.when] = report
    return report


@pytest.fixture
def driver(request: pytest.FixtureRequest) -> Iterator[WebDriver]:
    browser = request.config.getoption("--selenium-browser")
    web_driver = _start(browser, headed=request.config.getoption("--selenium-headed"))
    blocked: list[str] = []
    if _NAVIGATION is not None:
        _enforce_navigation(web_driver, _NAVIGATION, blocked)
    log.info("Starting %s in %s", request.node.nodeid, browser)
    try:
        web_driver.set_window_size(settings.VIEWPORT["width"], settings.VIEWPORT["height"])
        # Matches Playwright's auto-waiting for element lookups; explicit waits still come from web_wait.
        web_driver.implicitly_wait(settings.DEFAULT_TIMEOUT_MS / 1000)
        web_driver.set_page_load_timeout(settings.NAVIGATION_TIMEOUT_MS / 1000)
        yield web_driver
        call_report = request.node.stash.get(_REPORTS, {}).get("call")
        if blocked:
            # Never capture a page that is outside the allowlist.
            log.error("Blocked navigation outside the allowlist: %s", ", ".join(blocked))
        elif call_report is not None and call_report.failed:
            _save_screenshot(web_driver, request.node.nodeid)
            _save_dom(web_driver, request.node.nodeid)
    finally:
        web_driver.quit()
        log.info("Finished %s", request.node.nodeid)


def _enforce_navigation(web_driver: WebDriver, policy: NavigationPolicy, blocked: list[str]) -> None:
    """driver.get is checked before it runs, and the page it lands on (after redirects) is checked after.

    Selenium has no request interception, so requests made by the page itself are not filtered here.
    """
    get = web_driver.get

    def checked_get(url: str) -> None:
        policy.check_navigation(url)
        get(url)
        landed = web_driver.current_url
        if landed.startswith(("http:", "https:", "file:")) and not policy.allows_navigation(landed):
            blocked.append(landed)
            raise NavigationNotAllowedError(f"Navigation to {url!r} ended at {landed!r}, outside the allowlist")

    web_driver.get = checked_get  # type: ignore[method-assign]


def _start(browser: str, headed: bool) -> WebDriver:
    extra_args = os.environ.get("AM_BROWSER_ARGS", "").split()
    if browser == "firefox":
        options = webdriver.FirefoxOptions()
        if not headed:
            options.add_argument("-headless")
        return webdriver.Firefox(options=options)
    options = webdriver.EdgeOptions() if browser == "edge" else webdriver.ChromeOptions()
    if not headed:
        options.add_argument("--headless=new")
    for argument in extra_args:
        options.add_argument(argument)
    if os.environ.get("AM_CHROME_BINARY") and browser == "chrome":
        options.binary_location = os.environ["AM_CHROME_BINARY"]
    return webdriver.Edge(options=options) if browser == "edge" else webdriver.Chrome(options=options)


def _artifact_name(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)


def _save_screenshot(web_driver: WebDriver, nodeid: str) -> None:
    path = settings.SCREENSHOT_DIR / f"{_artifact_name(nodeid)}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        saved = web_driver.save_screenshot(str(path))
    except Exception:  # the session may already be gone; a missing screenshot must not hide the real failure
        log.exception("Could not save failure screenshot for %s", nodeid)
    else:
        if saved:
            log.error("Test failed; screenshot saved to %s", path)
        else:
            log.error("Test failed; could not save screenshot to %s", path)


def _save_dom(web_driver: WebDriver, nodeid: str) -> None:
    """The page HTML at failure time; the repair agent reads the available locators from it."""
    path = settings.DOM_DIR / f"{_artifact_name(nodeid)}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(web_driver.page_source, encoding="utf-8")
    except Exception:  # the session may already be gone
        log.exception("Could not save the DOM snapshot for %s", nodeid)
