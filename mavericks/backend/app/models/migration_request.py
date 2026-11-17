from app.models.base import CamelModel
from app.models.enums import TargetFramework
from app.models.fields import MigrationId


class MigrationRef(CamelModel):
    migration_id: MigrationId


class MigrationTargetRef(MigrationRef):
    target_framework: TargetFramework = TargetFramework.PLAYWRIGHT


class AnalyzeRequest(MigrationTargetRef):
    pass


class GenerateRequest(MigrationTargetRef):
    # DEMO MODE only: break one known locator on purpose so the self-healing loop can be shown.
    simulate_locator_failure: bool = False


class ValidateRequest(MigrationTargetRef):
    pass


class ExecuteRequest(MigrationTargetRef):
    pass


class RepairRequest(MigrationTargetRef):
    apply: bool = False


class QTestSyncRequest(MigrationRef):
    pass
