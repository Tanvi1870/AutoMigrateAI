"""Generate the sample login migration (samples/icap/login_test.json -> Playwright and Selenium) and validate it statically.

Usage (from the repository root):
    .venv/Scripts/python.exe scripts/generate_sample_login.py
"""

import ast
import sys
from pathlib import Path

from pyflakes.api import check
from pyflakes.reporter import Reporter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.keyword_resolver import KeywordResolver  # noqa: E402
from app.agents.locator_resolver import LocatorResolver  # noqa: E402
from app.dependencies import get_keyword_repository, get_parser  # noqa: E402
from app.generators.playwright_generator import PlaywrightGenerator  # noqa: E402
from app.generators.selenium_generator import SeleniumGenerator  # noqa: E402
from app.knowledge.loader import load_knowledge  # noqa: E402

MIGRATION_ID = "MIG-SAMPLELOGIN"
SOURCE = ROOT / "samples" / "icap" / "login_test.json"


def main() -> int:
    test_case = get_parser().parse(SOURCE.read_bytes(), source_name=SOURCE.name)
    resolvers = (
        KeywordResolver(get_keyword_repository()),
        LocatorResolver(load_knowledge("locator_rules")),
        load_knowledge("framework_templates"),
    )
    problems = 0
    for generator in (PlaywrightGenerator(*resolvers), SeleniumGenerator(*resolvers)):
        output_dir = ROOT / "generated" / generator.target / MIGRATION_ID
        artifacts = generator.generate(test_case, output_dir)
        print(f"Generated {len(artifacts)} files in {output_dir}")
        for artifact in artifacts:
            if artifact.path.endswith(".py"):
                ast.parse(artifact.content, filename=artifact.path)
                problems += check(artifact.content, artifact.path, Reporter(sys.stdout, sys.stderr))
    print("Validation passed" if problems == 0 else f"Validation found {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
