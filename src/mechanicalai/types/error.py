from __future__ import annotations

import pydantic
from pydantic import ConfigDict


class ErrorObject(pydantic.BaseModel):
    """The ``error`` member of every error answer. Handle by ``code``; messages are not stable."""

    model_config = ConfigDict(extra="allow")

    type: str
    code: str
    message: str
    param: str | None = None
    request_id: str | None = None
    limit: int | None = None
    """On project_limit_reached, file_limit_reached, storage_quota_reached and concurrency_limit_reached."""
    count: int | None = None
    """On the same codes."""
