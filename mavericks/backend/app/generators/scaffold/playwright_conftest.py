"""Browser and page setup, logging, failure screenshots and teardown for the generated Playwright tests.

Browser launch and teardown come from pytest-playwright (`--browser`, `--headed`, `--slowmo` options).
"""

import logging
import re
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Frame, Page, Route, expect

from config import settings
from config.navigation import NavigationPolicy

log = logging.getLogger("automigrate")
_REPORTS = pytest.StashKey[dict[str, pytest.TestReport]]()
# Set by AutoMigrate AI for every run; None when the project is run by hand outside the backend.
_NAVIGATION = NavigationPolicy.from_environment()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "viewport": settings.VIEWPORT}


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[pytest.TestReport]:
    report = yield
    item.stash.setdefault(_REPORTS, {})[report.when] = report
    return report


@pytest.fixture(autouse=True)
def page_setup(request: pytest.FixtureRequest, page: Page) -> Iterator[None]:
    page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
    page.set_default_navigation_timeout(settings.NAVIGATION_TIMEOUT_MS)
    expect.set_options(timeout=settings.DEFAULT_TIMEOUT_MS)
    blocked: list[str] = []
    if _NAVIGATION is not None:
        _enforce_navigation(page, _NAVIGATION, blocked)
    log.info("Starting %s", request.node.nodeid)
    yield
    call_report = request.node.stash.get(_REPORTS, {}).get("call")
    if blocked:
        # Never capture a page that is outside the allowlist.
        log.error("Blocked navigation outside the allowlist: %s", ", ".join(blocked))
    elif call_report is not None and call_report.failed:
        _save_screenshot(page, request.node.nodeid)
        _save_dom(page, request.node.nodeid)
    log.info("Finished %s", request.node.nodeid)
    if blocked:
        pytest.fail(f"NavigationNotAllowedError: blocked {', '.join(blocked)}", pytrace=False)


def _enforce_navigation(page: Page, policy: NavigationPolicy, blocked: list[str]) -> None:
    """page.goto is checked before it runs; every request (including clicks and redirects to new documents)
    is routed through the policy; a main frame that still lands outside the allowlist fails the test."""
    goto = page.goto

    def checked_goto(url: str, **kwargs: Any) -> Any:
        policy.check_navigation(url)
        return goto(url, **kwargs)

    page.goto = checked_goto  # type: ignore[method-assign]

    def route(route: Route) -> None:
        request = route.request
        if request.is_navigation_request():
            allowed = policy.allows_navigation(request.url)
        else:
            allowed = policy.allows_request(request.url)
        if allowed:
            route.continue_()
        else:
            blocked.append(request.url)
            route.abort("blockedbyclient")

    def landed(frame: Frame) -> None:
        url = frame.url
        checked = url.startswith(("http:", "https:", "file:"))
        if frame == page.main_frame and checked and not policy.allows_navigation(url):
            blocked.append(url)

    page.context.route("**/*", route)
    page.on("framenavigated", landed)


def _artifact_name(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)


def _save_screenshot(page: Page, nodeid: str) -> None:
    path = settings.SCREENSHOT_DIR / f"{_artifact_name(nodeid)}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:  # the page may already be closed; a missing screenshot must not hide the real failure
        log.exception("Could not save failure screenshot for %s", nodeid)
    else:
        log.error("Test failed; screenshot saved to %s", path)


def _save_dom(page: Page, nodeid: str) -> None:
    """The page HTML at failure time; the repair agent reads the available locators from it."""
    path = settings.DOM_DIR / f"{_artifact_name(nodeid)}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(page.content(), encoding="utf-8")
    except Exception:  # the page may already be closed
        log.exception("Could not save the DOM snapshot for %s", nodeid)
