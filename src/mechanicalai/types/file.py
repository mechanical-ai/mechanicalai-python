from __future__ import annotations

from datetime import datetime
from typing import Literal

from mechanicalai.types._base import BaseModel

FileStatus = Literal["uploading", "ready"]


class File(BaseModel):
    """What the customer uploaded, scoped to a project. An input to runs; what runs produce are artifacts."""

    id: str
    object: Literal["file"] = "file"
    project_id: str
    name: str
    size: int
    content_type: str
    digest: str | None = None
    """``sha256:<hex>``, set once the bytes are known."""
    status: FileStatus | str
    created_at: datetime
    expires_at: datetime | None = None
    """While ``uploading``: the pending upload is discarded an hour after this."""


class UploadUrl(BaseModel):
    """A pending file and where to PUT its bytes."""

    file: File
    upload_url: str
    upload_headers: dict[str, str] = {}
    expires_at: datetime


class DownloadLink(BaseModel):
    """A short-lived link to a file's or an artifact's bytes. The id is permanent; the link is not."""

    url: str
    expires_at: datetime
    format: str | None = None
    content_type: str
    size: int | None = None
