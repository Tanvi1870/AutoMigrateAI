"""Navigation allowlist for generated tests: which URLs a test may open or load.

The same file is used by the AutoMigrate AI backend (checks before code is generated) and copied into every
generated project as config/navigation.py (checks while the test runs). Standard library only.

An allowed entry is a URL prefix such as "https://app.example.com/shop/":
  * a navigation (page.goto, driver.get, a main-frame document load) must match the scheme, host, port
    and path prefix of an entry;
  * any other request (scripts, images, XHR) must go to the origin (scheme, host, port) of an entry.
URLs are normalised the way browsers resolve them (dot segments, default ports, case of scheme and host)
and anything ambiguous, such as credentials in the URL or encoded slashes and dots, is rejected.
"""

import json
import os
import posixpath
import re
from urllib.parse import urlsplit

ENV_VAR = "AM_ALLOWED_URLS"
_DEFAULT_PORTS = {"http": 80, "https": 443}
_ENCODED_SEPARATORS = re.compile(r"%(?:2e|2f|5c)", re.IGNORECASE)
_SLASHES = re.compile(r"/{2,}")


class NavigationNotAllowedError(ValueError):
    """The URL is not covered by the navigation allowlist."""


def normalise(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, path) of an absolute http(s) URL, or NavigationNotAllowedError."""
    text = url.strip()
    if any(ord(ch) < 0x21 or ch == "\\" for ch in text):
        raise NavigationNotAllowedError(f"{url!r} contains whitespace, control characters or backslashes")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise NavigationNotAllowedError(f"{url!r} is not a valid URL: {exc}") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise NavigationNotAllowedError(f"{url!r} must use http or https")
    if "@" in parts.netloc:
        raise NavigationNotAllowedError(f"{url!r} must not contain credentials")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise NavigationNotAllowedError(f"{url!r} has no host")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise NavigationNotAllowedError(f"{url!r} has an invalid host name") from None
    raw_path = parts.path or "/"
    if _ENCODED_SEPARATORS.search(raw_path):
        raise NavigationNotAllowedError(f"{url!r} contains encoded dots or slashes in its path")
    collapsed = _SLASHES.sub("/", raw_path)
    path = posixpath.normpath(collapsed)
    if collapsed.endswith("/") and not path.endswith("/"):
        path += "/"
    return scheme, host, port or _DEFAULT_PORTS[scheme], path


def _path_matches(path: str, prefix: str) -> bool:
    if prefix == "/":
        return True
    base = prefix.rstrip("/")
    return path == base or path.startswith(base + "/")


class NavigationPolicy:
    def __init__(self, allowed_prefixes: list[str]) -> None:
        self.allowed_prefixes = list(allowed_prefixes)
        self._entries = [normalise(prefix) for prefix in self.allowed_prefixes]

    @classmethod
    def from_environment(cls) -> "NavigationPolicy | None":
        """The policy the backend passed to this run, or None when the project runs outside AutoMigrate AI."""
        raw = os.environ.get(ENV_VAR)
        if raw is None:
            return None
        return cls(json.loads(raw))

    def to_environment(self) -> str:
        return json.dumps(self.allowed_prefixes)

    def allows_navigation(self, url: str) -> bool:
        try:
            scheme, host, port, path = normalise(url)
        except NavigationNotAllowedError:
            return False
        return any(
            (scheme, host, port) == entry[:3] and _path_matches(path, entry[3]) for entry in self._entries
        )

    def allows_request(self, url: str) -> bool:
        try:
            origin = normalise(url)[:3]
        except NavigationNotAllowedError:
            return url.startswith(("data:", "blob:", "about:"))  # in-page resources never leave the browser
        return any(origin == entry[:3] for entry in self._entries)

    def check_navigation(self, url: str) -> None:
        if not self.allows_navigation(url):
            raise NavigationNotAllowedError(
                f"Navigation to {url!r} is blocked: it is not under an allowed URL ({', '.join(self.allowed_prefixes)})"
            )
