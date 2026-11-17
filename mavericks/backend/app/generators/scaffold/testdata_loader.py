"""Runtime test data: values from the JSON file, overridable with AM_DATA_<NAME> environment variables.

Sensitive values are not in the data file (they are null there). They come from AM_DATA_<NAME> or from the
local secrets file named in the data file's "secretsFile", which is never part of the published artifacts.
"""

import json
import os
import re
from pathlib import Path

ENV_PREFIX = "AM_DATA_"
_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class DataSet:
    def __init__(self, variables: dict[str, str | None], secrets: dict[str, str] | None = None) -> None:
        self._variables = dict(variables)
        self._secrets = dict(secrets or {})

    @classmethod
    def load(cls, file_name: str) -> "DataSet":
        folder = Path(__file__).parent
        content = json.loads((folder / file_name).read_text(encoding="utf-8"))
        secrets: dict[str, str] = {}
        secrets_file = content.get("secretsFile")
        if secrets_file and (folder / secrets_file).is_file():
            secrets = json.loads((folder / secrets_file).read_text(encoding="utf-8"))["secrets"]
        return cls(content["variables"], secrets)

    def get(self, name: str) -> str:
        override = os.environ.get(ENV_PREFIX + name)
        if override is not None:
            return override
        if name not in self._variables:
            raise KeyError(f"Test data variable {name!r} is not defined")
        value = self._variables[name]
        if value is not None:
            return value
        if name in self._secrets:
            return self._secrets[name]
        raise KeyError(
            f"Sensitive test data {name!r} is not available: set the {ENV_PREFIX}{name} environment variable"
        )

    def resolve(self, text: str) -> str:
        """Replace every ${NAME} in text with its value."""
        return _REFERENCE.sub(lambda match: self.get(match.group(1)), text)
