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
    """The account's uploads and artifacts together."""
    tool_runs: int
    """The account's external tool runs in flight, app and API alike, as ``concurrent_tool_runs`` counts them."""
    running_runs: int
    """The account's API runs queued or running, across its keys."""


class Limits(BaseModel):
    """The app's own numbers under the names its rules give them, plus the three that exist for the API alone
    (``files_per_project``, ``storage_bytes``, ``requests_per_minute``)."""

    object: Literal["limits"] = "limits"
    projects_per_user: int
    """Projects per account, app and API alike."""
    files_per_project: int
    """Files an API project holds, pending uploads included."""
    storage_bytes: int
    """The account's uploads and artifacts together."""
    concurrent_tool_runs: int
    """External tool runs in flight per account, app and API runs together."""
    messages_per_minute: int
    """Messages a minute per account, app messages and API runs together: the burst a create or a reply may hit
    (429 ``rate_limited``, retried after ``Retry-After``)."""
    requests_per_minute: int
    """Requests per key, enforced at the edge."""
    reference_file_upload_bytes: int
    """The app's reference-file limit: the multipart upload's bound."""
    user_char_limit: int
    """The engine's message bound: what it keeps of a longer ``problem``."""
    usage: LimitsUsage
