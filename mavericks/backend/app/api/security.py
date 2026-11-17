"""API authentication: a shared API key in the X-API-Key header.

Every route except /api/health requires the key. Running without a key is only allowed for
ENVIRONMENT=local (enforced in Settings), so a demo or shared deployment can never start unauthenticated.
"""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, description="Shared API key (see README)")


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    provided: Annotated[str | None, Security(_api_key_header)] = None,
) -> None:
    expected = settings.api_key.get_secret_value() if settings.api_key else None
    if expected is None:
        return
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"A valid {API_KEY_HEADER} header is required",
        )
