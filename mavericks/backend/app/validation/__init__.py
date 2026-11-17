from typing import Any

from app.agents.keyword_resolver import KeywordResolver
from app.agents.locator_resolver import LocatorResolver
from app.validation.engine import ValidationContext, ValidationEngine, Validator
from app.validation.validators import (
    ImportValidator,
    KeywordValidator,
    LocatorValidator,
    StandardsValidator,
    SyntaxValidator,
)

__all__ = [
    "ImportValidator",
    "KeywordValidator",
    "LocatorValidator",
    "StandardsValidator",
    "SyntaxValidator",
    "ValidationContext",
    "ValidationEngine",
    "Validator",
    "create_validation_engine",
]


def create_validation_engine(
    keyword_resolver: KeywordResolver,
    locator_resolver: LocatorResolver,
    coding_standards: dict[str, Any],
    framework_templates: dict[str, Any],
) -> ValidationEngine:
    return ValidationEngine(
        [
            SyntaxValidator(),
            ImportValidator(coding_standards, framework_templates),
            KeywordValidator(keyword_resolver),
            LocatorValidator(locator_resolver),
            StandardsValidator(coding_standards),
        ]
    )
