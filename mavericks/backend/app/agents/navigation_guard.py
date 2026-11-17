"""Plan- and generation-time checks for every URL a test case navigates to.

* Every navigation URL, with its ${VAR} test data filled in, must be under ALLOWED_TARGET_URLS, so an uploaded
  test cannot point the server's browser at internal services, cloud metadata endpoints or local files.
* Sensitive test data (passwords, tokens, ...) must never be part of a navigation URL: a URL is sent to the
  target host and ends up in its logs, and AM_DATA_<NAME> overrides would otherwise leak server-side values.

Generated tests enforce the same policy again at run time (config/navigation.py), because AM_DATA_<NAME>
environment overrides can change test data after generation.
"""

from collections.abc import Iterable

from app.generators.scaffold.navigation_policy import NavigationPolicy
from app.knowledge.keyword_definitions import KeywordParameter, RuleType
from app.knowledge.keyword_repository import KeywordRepository
from app.models.canonical_test import CanonicalTestCase
from app.models.fields import VARIABLE_REFERENCE


class NavigationGuard:
    def __init__(self, policy: NavigationPolicy, keyword_repository: KeywordRepository) -> None:
        self.policy = policy
        # Keywords whose 'value' is a URL, according to the knowledge base (e.g. web_navigate).
        self._url_keywords = frozenset(
            name
            for name in keyword_repository.keywords
            if any(
                rule.rule is RuleType.URL and rule.parameter is KeywordParameter.VALUE
                for rule in keyword_repository.get(name).validation_rules  # type: ignore[union-attr]
            )
        )

    @classmethod
    def from_prefixes(cls, allowed_prefixes: Iterable[str], keyword_repository: KeywordRepository) -> "NavigationGuard":
        return cls(NavigationPolicy(list(allowed_prefixes)), keyword_repository)

    def problems(self, test_case: CanonicalTestCase) -> list[str]:
        data = test_case.test_data
        problems = []
        for step in test_case.steps:
            if step.keyword not in self._url_keywords or step.value is None:
                continue
            references = VARIABLE_REFERENCE.findall(step.value)
            sensitive = sorted({name for name in references if data.is_sensitive(name)})
            if sensitive:
                names = ", ".join(f"${{{name}}}" for name in sensitive)
                problems.append(f"Step {step.sequence}: sensitive test data ({names}) must not be used in a URL")
                continue
            if any(name not in data.variables for name in references):
                continue  # reported as an unresolved variable
            url = VARIABLE_REFERENCE.sub(lambda match: data.variables[match.group(1)], step.value)
            if not self.policy.allows_navigation(url):
                problems.append(
                    f"Step {step.sequence}: {url!r} is not an allowed navigation target; allowed URL prefixes: "
                    f"{', '.join(self.policy.allowed_prefixes)} (setting ALLOWED_TARGET_URLS)"
                )
        return problems
