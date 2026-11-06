from __future__ import annotations

from datetime import datetime
from typing import Literal

from mechanicalai.types._base import BaseModel

EngineStatus = Literal["current"]


class Engine(BaseModel):
    """An engine the key may use: the alias and the deployed snapshot it points to."""

    id: str
    """The name a run asks for: ``mekint``."""
    object: Literal["engine"] = "engine"
    snapshot: str
    """The deployed snapshot the alias points to, named by the day it became current: ``mekint-20261105``."""
    status: EngineStatus | str
    """``current`` today; other states may come with pinned snapshots."""
    released_at: datetime
