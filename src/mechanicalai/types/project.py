from __future__ import annotations

from datetime import datetime
from typing import Literal

from typing_extensions import TypedDict

from mechanicalai.types._base import BaseModel

Metadata = dict[str, str]
"""At most 16 keys; names up to 64 characters, values up to 512. Stored and echoed, never read by the engine."""


class Project(BaseModel):
    """The durable container: files in, runs on it, artifacts out."""

    id: str
    object: Literal["project"] = "project"
    name: str | None = None
    """The engine names an unnamed project on its first run."""
    app_url: str
    """The project in the app."""
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None = None
    metadata: Metadata = {}


class ProjectCreateParams(TypedDict, total=False):
    name: str | None
    metadata: Metadata


class ProjectUpdateParams(TypedDict, total=False):
    name: str | None
    metadata: Metadata
