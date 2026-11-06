from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import PrivateAttr

from mechanicalai.types._base import BaseModel
from mechanicalai.types.run import Run

WebhookEventType = Literal["run.succeeded", "run.needs_input", "run.budget_exhausted", "run.failed", "run.cancelled"]


class WebhookEventData(BaseModel):
    run: Run


class WebhookEvent(BaseModel):
    """The body posted to a run's ``webhook_url`` on its terminal transition (``id`` is ``wh_...``)."""

    id: str
    object: Literal["event"] = "event"
    type: WebhookEventType | str
    created_at: datetime
    data: WebhookEventData

    _key_id: str | None = PrivateAttr(default=None)

    @property
    def key_id(self) -> str | None:
        """Which key's webhook secret signed the delivery (``Mechanicalai-Key-Id``), when ``verify`` read it."""
        return self._key_id
