"""Locators available in a failure-time DOM snapshot, ranked by similarity to the locator that failed.

The HTML comes from the application under test, so it is only parsed (never executed) and size-limited.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.models.canonical_test import Locator
from app.models.enums import LocatorStrategy
from app.models.repair import CandidateLocator

MAX_DOM_CHARS = 2_000_000
MAX_CANDIDATES = 20
_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"})
_SKIPPED_TAGS = frozenset({"script", "style", "template", "head", "title", "html", "body", "form", "meta"})
_TEXT_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "div", "label", "li", "td"})
_FIELD_TAGS = frozenset({"input", "textarea", "select"})
_SYNONYMS = {"btn": "button", "lnk": "link", "txt": "text", "msg": "message", "pwd": "password", "usr": "user"}
_BUTTON_HINTS = frozenset({"button", "submit"})
_FIELD_HINTS = frozenset({"username", "user", "password", "field", "input", "email", "name"})
_TEXT_HINTS = frozenset({"message", "text", "title", "heading", "label", "welcome", "error"})
_STRATEGY_BONUS = {LocatorStrategy.DATA_TESTID: 0.1, LocatorStrategy.ID: 0.05}


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    text: str = ""
    labels: list[str] = field(default_factory=list)

    @property
    def display_text(self) -> str | None:
        for text in (self.text, *self.labels, self.attrs.get("aria-label"), self.attrs.get("placeholder")):
            if text and text.strip():
                return " ".join(text.split())[:120]
        if self.tag == "input" and self.attrs.get("type") in ("submit", "button"):
            return self.attrs.get("value")
        return None

    def matches(self, strategy: LocatorStrategy, value: str) -> bool:
        attribute = {LocatorStrategy.ID: "id", LocatorStrategy.NAME: "name", LocatorStrategy.DATA_TESTID: "data-testid"}
        return strategy in attribute and self.attrs.get(attribute[strategy]) == value


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[Element] = []
        self._open: list[Element] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "template"):
            self._skip_depth += 1
        if self._skip_depth or tag in _SKIPPED_TAGS:
            return
        element = Element(tag=tag, attrs={name: value or "" for name, value in attrs})
        self.elements.append(element)
        if tag not in _VOID_TAGS:
            self._open.append(element)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "template"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index].tag == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            for element in self._open:
                element.text += data


def parse_dom(html: str) -> list[Element]:
    collector = _Collector()
    collector.feed(html[:MAX_DOM_CHARS])
    collector.close()
    by_id = {e.attrs["id"]: e for e in collector.elements if e.attrs.get("id")}
    for element in collector.elements:
        if element.tag == "label" and element.attrs.get("for") in by_id:
            by_id[element.attrs["for"]].labels.append(element.text)
    return collector.elements


def locator_present(elements: list[Element], locator: Locator) -> bool | None:
    """Whether the locator still matches the page; None when it cannot be checked statically (css/xpath)."""
    if locator.strategy not in (LocatorStrategy.ID, LocatorStrategy.NAME, LocatorStrategy.DATA_TESTID):
        return None
    return any(element.matches(locator.strategy, locator.value) for element in elements)


def rank_candidates(
    elements: list[Element],
    failed: Locator,
    object_name: str,
    repository: dict[str, Locator],
    allowed: frozenset[LocatorStrategy],
) -> list[CandidateLocator]:
    hints = _tokens(object_name) | _tokens(failed.value)
    taken = {(loc.strategy, loc.value) for name, loc in repository.items() if name != object_name}
    scored: dict[tuple[LocatorStrategy, str], tuple[float, Element]] = {}
    for element in elements:
        for strategy, value in _locators_for(element):
            key = (strategy, value)
            if strategy not in allowed or key == (failed.strategy, failed.value):
                continue
            score = _score(element, strategy, value, hints)
            if key in taken:
                score *= 0.3  # another object already owns this element
            if key not in scored or score > scored[key][0]:
                scored[key] = (score, element)
    ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0][0], item[0][1]))[:MAX_CANDIDATES]
    return [
        CandidateLocator(
            candidate_id=index,
            strategy=strategy,
            value=value,
            tag=element.tag,
            text=element.display_text,
            score=round(score, 3),
        )
        for index, ((strategy, value), (score, element)) in enumerate(ranked)
    ]


def _locators_for(element: Element) -> list[tuple[LocatorStrategy, str]]:
    found = []
    if value := element.attrs.get("data-testid", "").strip():
        found.append((LocatorStrategy.DATA_TESTID, value))
    if (value := element.attrs.get("id", "").strip()) and not any(ch.isspace() for ch in value):
        found.append((LocatorStrategy.ID, value))
    if (value := element.attrs.get("name", "").strip()) and element.tag in (*_FIELD_TAGS, "button"):
        found.append((LocatorStrategy.NAME, value))
    return found


def _score(element: Element, strategy: LocatorStrategy, value: str, hints: set[str]) -> float:
    if not hints:
        return 0.0
    tokens = _tokens(value) | _tokens(element.display_text or "") | {element.tag}
    if element.tag == "input":
        tokens.add(element.attrs.get("type", "text"))
    score = len(hints & tokens) / len(hints)
    if hints & _BUTTON_HINTS and (
        element.tag == "button" or (element.tag == "input" and element.attrs.get("type") in ("submit", "button"))
    ):
        score += 0.25
    elif hints & _FIELD_HINTS and element.tag in _FIELD_TAGS:
        score += 0.25
    elif hints & _TEXT_HINTS and element.tag in _TEXT_TAGS:
        score += 0.15
    if score > 0:
        score += _STRATEGY_BONUS.get(strategy, 0.0)
    return min(score, 1.0)


def _tokens(text: str) -> set[str]:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    tokens = {word.lower() for word in re.split(r"[^A-Za-z0-9]+", words) if len(word) > 1}
    return {_SYNONYMS.get(token, token) for token in tokens}
