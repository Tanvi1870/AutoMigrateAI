from pydantic import Field

from app.models.base import CamelModel
from app.models.canonical_test import Locator
from app.models.fields import Keyword, Name

DEMO_LABEL = "DEMO MODE"


class DemoFault(CamelModel):
    """A deliberate, opt-in locator fault for presentations; never applied unless explicitly requested."""

    label: str = DEMO_LABEL
    step_sequence: int = Field(ge=1)
    keyword: Keyword
    object_name: Name
    original_locator: Locator
    injected_locator: Locator
    description: str
    # Repairs recorded before the fault was injected are replayed underneath it, later ones on top.
    # Persisted with the record: current_test_case() needs it after a restart.
    repairs_before: int = Field(default=0, ge=0)
