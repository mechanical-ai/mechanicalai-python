from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from mechanicalai.types._base import BaseModel

UsagePeriod = Literal["day", "month"]


class UsageByEndUser(BaseModel):
    runs: int
    cost_cents: int


class UsageBucket(BaseModel):
    """One period of the window."""

    from_: datetime = Field(alias="from")
    to: datetime
    runs: int
    cost_cents: int


class Usage(BaseModel):
    """The account's API usage over a window of UTC calendar periods."""

    object: Literal["usage"] = "usage"
    period: UsagePeriod | str
    from_: datetime = Field(alias="from")
    to: datetime
    runs: int
    cost_cents: int
    buckets: list[UsageBucket] = []
    """One per period in the window."""
    by_end_user: dict[str, UsageByEndUser] = {}
    """With ``group_by="end_user"``: runs that named an ``end_user``; runs without one are in the totals only."""


class LimitsUsage(BaseModel):
    projects: int
    """App and API projects alike, as the limit counts them."""
    storage_bytes: int
    running_runs: int
    running_runs_this_key: int


class Limits(BaseModel):
    """The account's limits and current counts."""

    object: Literal["limits"] = "limits"
    projects_per_account: int
    files_per_project: int
    files_per_run: int
    storage_bytes: int
    concurrent_runs_per_key: int
    requests_per_minute: int
    upload_bytes_max: int
    problem_chars_max: int
    instructions_chars_max: int
    usage: LimitsUsage
