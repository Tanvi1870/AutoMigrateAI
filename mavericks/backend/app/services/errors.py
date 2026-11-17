class MigrationStateError(RuntimeError):
    """The migration has not reached the stage this operation needs (mapped to HTTP 409)."""
