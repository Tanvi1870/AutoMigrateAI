"""Run settings for the generated project. Each value can be overridden with an environment variable."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = Path(os.environ.get("AM_ARTIFACTS_DIR", PROJECT_ROOT / "artifacts"))
SCREENSHOT_DIR = ARTIFACTS_DIR / "screenshots"
DOM_DIR = ARTIFACTS_DIR / "dom"
DEFAULT_TIMEOUT_MS = int(os.environ.get("AM_TIMEOUT_MS", "10000"))
NAVIGATION_TIMEOUT_MS = int(os.environ.get("AM_NAVIGATION_TIMEOUT_MS", "30000"))
VIEWPORT = {
    "width": int(os.environ.get("AM_VIEWPORT_WIDTH", "1280")),
    "height": int(os.environ.get("AM_VIEWPORT_HEIGHT", "720")),
}
