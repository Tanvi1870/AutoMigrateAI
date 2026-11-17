"""Which locator strategies and target frameworks each application type supports.

Windows and terminal-emulator entries are design placeholders: no target framework supports them yet,
so plans for those applications are always BLOCKED until an adapter is added here.
"""

from collections.abc import Mapping
from types import MappingProxyType

from app.models.enums import ApplicationType, LocatorStrategy, TargetFramework

LOCATOR_STRATEGIES: Mapping[ApplicationType, frozenset[LocatorStrategy]] = MappingProxyType(
    {
        ApplicationType.WEB: frozenset(
            {
                LocatorStrategy.ID,
                LocatorStrategy.CSS,
                LocatorStrategy.XPATH,
                LocatorStrategy.NAME,
                LocatorStrategy.DATA_TESTID,
            }
        ),
        ApplicationType.WINDOWS: frozenset(
            {
                LocatorStrategy.AUTOMATION_ID,
                LocatorStrategy.NAME,
                LocatorStrategy.CLASS_NAME,
                LocatorStrategy.XPATH,
            }
        ),
        ApplicationType.TERMINAL_EMULATOR: frozenset(
            {
                LocatorStrategy.SCREEN_POSITION,
                LocatorStrategy.FIELD_LABEL,
            }
        ),
    }
)

SUPPORTED_TARGETS: Mapping[ApplicationType, frozenset[TargetFramework]] = MappingProxyType(
    {
        ApplicationType.WEB: frozenset({TargetFramework.PLAYWRIGHT, TargetFramework.SELENIUM}),
        ApplicationType.WINDOWS: frozenset(),
        ApplicationType.TERMINAL_EMULATOR: frozenset(),
    }
)
