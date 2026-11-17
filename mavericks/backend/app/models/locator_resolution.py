from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.models.base import CamelModel
from app.models.canonical_test import Locator
from app.models.fields import Name

SeleniumBy = Literal["ID", "NAME", "XPATH", "CSS_SELECTOR", "CLASS_NAME", "TAG_NAME", "LINK_TEXT", "PARTIAL_LINK_TEXT"]


class LocatorStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


class UnresolvedLocatorReason(StrEnum):
    MISSING_FROM_REPOSITORY = "MISSING_FROM_REPOSITORY"
    UNSUPPORTED_STRATEGY = "UNSUPPORTED_STRATEGY"


class LocatorResolution(CamelModel):
    """Framework-neutral outcome of resolving one object name. Only RESOLVED results carry a locator."""

    object_name: Name
    status: LocatorStatus
    locator: Locator | None = None
    step_sequences: list[int] = Field(default_factory=list)
    reason: UnresolvedLocatorReason | None = None
    message: str | None = None
    suggestions: list[str] = Field(default_factory=list)  # hints for the report only, never auto-applied
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return self.status is LocatorStatus.RESOLVED

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.is_resolved:
            if self.locator is None or self.reason is not None:
                raise ValueError("a RESOLVED locator needs a locator and no unresolved reason")
            if self.locator.name != self.object_name:
                raise ValueError("locator name must match the resolved object name")
        elif self.locator is not None or self.reason is None or not self.message:
            raise ValueError("an UNRESOLVED locator needs a reason and a message, and must not carry a locator")
        return self


class PlaywrightLocator(CamelModel):
    model_config = ConfigDict(frozen=True)

    selector: Name

    @property
    def expression(self) -> str:
        return f"page.locator({self.selector!r})"


class SeleniumLocator(CamelModel):
    model_config = ConfigDict(frozen=True)

    by: SeleniumBy
    value: Name

    @property
    def expression(self) -> str:
        return f"(By.{self.by}, {self.value!r})"
