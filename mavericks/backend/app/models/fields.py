import re
from typing import Annotated

from pydantic import StringConstraints

# Single-line, non-empty, no leading/trailing whitespace (IDs, names, locator values).
Name = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
# Free text that must not be blank (diagnoses, explanations).
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Keyword = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]
VariableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

VARIABLE_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Server-generated IDs only; the strict pattern also prevents path traversal when IDs map to folders.
MIGRATION_ID_PATTERN = r"^MIG-[A-Za-z0-9]{1,32}$"
MigrationId = Annotated[str, StringConstraints(pattern=MIGRATION_ID_PATTERN)]
