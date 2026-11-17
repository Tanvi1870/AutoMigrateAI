from enum import StrEnum


class ApplicationType(StrEnum):
    WEB = "WEB"
    WINDOWS = "WINDOWS"  # future adapter, not converted in MVP
    TERMINAL_EMULATOR = "TERMINAL_EMULATOR"  # future adapter, not converted in MVP


class TargetFramework(StrEnum):
    PLAYWRIGHT = "playwright"
    SELENIUM = "selenium"


class LocatorStrategy(StrEnum):
    # Web
    ID = "id"
    CSS = "css"
    XPATH = "xpath"
    NAME = "name"
    DATA_TESTID = "data-testid"
    # Windows desktop (future adapter)
    AUTOMATION_ID = "automation-id"
    CLASS_NAME = "class-name"
    # Terminal emulator (future adapter)
    SCREEN_POSITION = "screen-position"
    FIELD_LABEL = "field-label"


class LocatorSource(StrEnum):
    OBJECT_REPOSITORY = "OBJECT_REPOSITORY"
    USER = "USER"
    AI_INFERRED = "AI_INFERRED"
    AI_REPAIRED = "AI_REPAIRED"


class PlanStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARNING"
    SKIPPED = "SKIPPED"


class ExecutionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ValidationCheck(StrEnum):
    """Values match the per-check field names on ValidationResult."""

    SYNTAX = "syntax"
    IMPORTS = "imports"
    KEYWORDS = "keywords"
    LOCATORS = "locators"
    VARIABLES = "variables"
    ASSERTIONS = "assertions"
    DUPLICATES = "duplicates"
    FRAMEWORK = "framework"
    STANDARDS = "standards"


class ArtifactKind(StrEnum):
    TEST = "TEST"
    PAGE_OBJECT = "PAGE_OBJECT"
    TEST_DATA = "TEST_DATA"
    CONFIG = "CONFIG"
    CONFTEST = "CONFTEST"
    PYTEST_CONFIG = "PYTEST_CONFIG"
    REQUIREMENTS = "REQUIREMENTS"
    OTHER = "OTHER"


class RepairChangeType(StrEnum):
    LOCATOR = "LOCATOR"
    WAIT = "WAIT"


class RepairStatus(StrEnum):
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    PASSED_AFTER_REPAIR = "PASSED_AFTER_REPAIR"  # applied and the re-run passed; not proof the test intent is unchanged
    FAILED = "FAILED"  # applied but the re-run still failed
