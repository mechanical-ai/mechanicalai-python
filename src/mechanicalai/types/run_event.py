from __future__ import annotations

from datetime import datetime
from typing import Literal

from mechanicalai.types._base import BaseModel
from mechanicalai.types.run import RunStatus

RunEventKind = Literal["phase", "progress", "task", "note", "status"]
RunPhase = Literal["thinking", "working"]


class RunEvent(BaseModel):
    """Progress while a run works. ``seq`` orders the events of one run; the last one is a terminal ``status``."""

    id: str
    object: Literal["run_event"] = "run_event"
    run_id: str
    seq: int
    at: datetime
    kind: RunEventKind | str
    """One of the five known kinds; an unknown kind is passed through."""
    phase: RunPhase | str | None = None
    """Set on ``phase`` events."""
    text: str | None = None
    """Set on ``progress``, ``task`` and ``note`` events: a plain sentence in the run's language."""
    percent: float | None = None
    """Set on ``task`` events: 0 to 100."""
    eta_s: float | None = None
    """Set on ``task`` events."""
    status: RunStatus | str | None = None
    """Set on ``status`` events: the run changed state."""
