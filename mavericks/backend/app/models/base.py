from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base model that (de)serialises using the camelCase keys of the API and sample files."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StrictCamelModel(CamelModel):
    """Canonical input models: unknown keys are rejected so source-mapping mistakes surface early."""

    model_config = ConfigDict(extra="forbid")
