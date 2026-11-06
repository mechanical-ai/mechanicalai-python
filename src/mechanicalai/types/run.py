from __future__ import annotations

from datetime import datetime
from typing import Literal

from typing_extensions import Required, TypedDict

from mechanicalai.types._base import BaseModel
from mechanicalai.types.project import Metadata

RunStatus = Literal["queued", "running", "needs_input", "succeeded", "budget_exhausted", "failed", "cancelled"]
RunFailureCode = Literal[
    "model_unavailable",
    "model_rejected",
    "internal_error",
    "step_budget",
    "tool_call_budget",
    "restarted",
    "engine_error",
    "engine_gave_up",
]
Units = Literal["metric", "imperial"]
Language = Literal["en", "ko"]

TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {"needs_input", "succeeded", "budget_exhausted", "failed", "cancelled"}
)
"""The statuses a run ends in. ``needs_input`` ends the run; ``runs.reply`` starts a new one on the same project."""


class RunFailure(BaseModel):
    code: RunFailureCode | str
    message: str


class Run(BaseModel):
    """One job on one project: a problem and files in; a report, models, drawings, tables and data out."""

    id: str
    object: Literal["run"] = "run"
    project_id: str
    parent_run_id: str | None = None
    engine: str
    """As requested: ``mekint``."""
    engine_version: str | None = None
    """The snapshot that ran, once started."""
    status: RunStatus | str
    """One of the seven known statuses; a value the library does not know yet is passed through."""
    cancel_requested: bool = False
    problem: str
    instructions: str | None = None
    files: list[str] = []
    units: Units | str = "metric"
    language: Language | str = "en"
    allow_web: bool = False
    budget_cents: int
    cost_cents: int = 0
    """Cost so far; final once the run is terminal."""
    summary: str | None = None
    next_actions: list[str] = []
    question: str | None = None
    """Set when the status is ``needs_input``."""
    failure: RunFailure | None = None
    """Set when the status is ``failed``."""
    report_artifact_id: str | None = None
    end_user: str | None = None
    metadata: Metadata = {}
    webhook_url: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


class RunCreateParams(TypedDict, total=False):
    """The body of ``POST /projects/{id}/runs``."""

    engine: Required[str]
    """``mekint``: the alias of the current snapshot (``engine_version`` on the run says which one ran)."""
    problem: Required[str]
    """1 to 50,000 characters."""
    budget_cents: Required[int]
    """At least 100: the most this run may charge."""
    files: list[str]
    """File ids in this project, each ``ready``."""
    instructions: str
    """Standing text for this run, up to 20,000 characters."""
    units: Units
    language: Language
    allow_web: bool
    end_user: str
    metadata: Metadata
    webhook_url: str
    """``https`` only; receives this run's terminal event."""


class RunReplyParams(TypedDict, total=False):
    """The body of ``POST /runs/{id}/reply``."""

    text: Required[str]
    files: list[str]
    budget_cents: int
    """Defaults to the replied run's budget."""
