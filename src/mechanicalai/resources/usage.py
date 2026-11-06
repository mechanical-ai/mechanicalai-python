from __future__ import annotations

from datetime import datetime
from typing import Literal

from mechanicalai.resources._base import AsyncAPIResource, SyncAPIResource, iso, parse_model
from mechanicalai.types.usage import Limits, Usage, UsagePeriod

__all__ = ["AsyncUsageResource", "UsageResource"]


class UsageResource(SyncAPIResource):
    def retrieve(
        self,
        *,
        period: UsagePeriod | None = None,
        from_: datetime | str | None = None,
        to: datetime | str | None = None,
        group_by: Literal["end_user"] | None = None,
    ) -> Usage:
        """The account's API usage: runs and cost over the period, by end user when ``group_by="end_user"``."""
        body, request_id = self._client._get_json(
            "/usage", params={"period": period, "from": iso(from_), "to": iso(to), "group_by": group_by}
        )
        return parse_model(Usage, body, request_id)

    def limits(self) -> Limits:
        """The account's limits and current counts."""
        body, request_id = self._client._get_json("/limits")
        return parse_model(Limits, body, request_id)


class AsyncUsageResource(AsyncAPIResource):
    async def retrieve(
        self,
        *,
        period: UsagePeriod | None = None,
        from_: datetime | str | None = None,
        to: datetime | str | None = None,
        group_by: Literal["end_user"] | None = None,
    ) -> Usage:
        """The account's API usage: runs and cost over the period, by end user when ``group_by="end_user"``."""
        body, request_id = await self._client._get_json(
            "/usage", params={"period": period, "from": iso(from_), "to": iso(to), "group_by": group_by}
        )
        return parse_model(Usage, body, request_id)

    async def limits(self) -> Limits:
        """The account's limits and current counts."""
        body, request_id = await self._client._get_json("/limits")
        return parse_model(Limits, body, request_id)
