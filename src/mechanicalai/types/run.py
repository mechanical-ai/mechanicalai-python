from __future__ import annotations

from datetime import datetime
from typing import Literal

from typing_extensions import Required, TypedDict

from mechanicalai.types._base import BaseModel
from mechanicalai.types.project import Metadata

RunStatus = Literal["queued", "running", "needs_input", "succeeded", "failed", "cancelled"]
RunFailureCode = Literal[
    "model_unavailable",
    "model_rejected",
    "internal_error",
    "step_budget",
    "tool_call_budget",
    "restarted",
    "engine_error",
    "engine_gave_up",
    "insufficient_credit",
    "key_cap_reached",
]
Units = Literal["metric", "imperial"]
Language = Literal["en", "ko"]

TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"needs_input", "succeeded", "failed", "cancelled"})
"""The statuses a run ends in. ``needs_input`` ends the run; ``runs.reply`` starts a new one on the same project."""


class RunFailure(BaseModel):
    code: RunFailureCode | str
    """The two stops for money are ``insufficient_credit`` (the API wallet reached zero while the run worked) and
    ``key_cap_reached`` (the key's monthly cap)."""
    message: str


class Run(BaseModel):
    """One message to the engine on a project: the problem and files in; it asks when it needs something and ends
    with its summary, next actions and the artifacts it made."""

    id: str
    object: Literal["run"] = "run"
    project_id: str
    parent_run_id: str | None = None
    engine: str
    """As requested: ``mekint``."""
    engine_version: str | None = None
    """The snapshot that ran, once started."""
    status: RunStatus | str
    """One of the six known statuses; a value the library does not know yet is passed through."""
    cancel_requested: bool = False
    problem: str
    files: list[str] = []
    units: Units | str = "metric"
    language: Language | str = "en"
    cost_cents: int = 0
    """The sum of the wallet's charges under the run so far; it may still rise for about a minute after the terminal
    event while the last charges land."""
    message: str | None = None
    """The engine's final reply, exactly the chat's last assistant bubble: null until the run ends, the question on
    ``needs_input``, null on a cancelled run and on one stopped for money."""
    summary: str | None = None
    """The engine's own summary (the first paragraph of its reply when it gave none); null on a cancelled run and on
    one stopped for money."""
    next_actions: list[str] = []
    question: str | None = None
    """Set when the status is ``needs_input``."""
    failure: RunFailure | None = None
    """Set when the status is ``failed``."""
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
    """The run's message, as typed in the app; the engine keeps the first ``user_char_limit`` characters of a longer
    one, as it does in the app."""
    files: list[str]
    """File ids in this project, each ``ready``: the files attached to the message."""
    units: Units
    language: Language
    end_user: str
    metadata: Metadata
    webhook_url: str
    """``https`` only; receives this run's terminal event."""


class RunReplyParams(TypedDict, total=False):
    """The body of ``POST /runs/{id}/reply``."""

    text: Required[str]
    files: list[str]
    """Added to the parent run's files."""
