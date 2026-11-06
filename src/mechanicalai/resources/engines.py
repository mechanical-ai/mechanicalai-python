from __future__ import annotations

from mechanicalai.pagination import AsyncPage, SyncPage
from mechanicalai.resources._base import AsyncAPIResource, SyncAPIResource
from mechanicalai.types.engine import Engine

__all__ = ["AsyncEngines", "Engines"]


class Engines(SyncAPIResource):
    def list(self) -> SyncPage[Engine]:
        """The engines this key may use: the alias ``mekint`` and the deployed snapshot it points to."""
        body, request_id = self._client._get_json("/engines")
        return SyncPage.from_body(body, Engine, request_id=request_id)


class AsyncEngines(AsyncAPIResource):
    async def list(self) -> AsyncPage[Engine]:
        """The engines this key may use: the alias ``mekint`` and the deployed snapshot it points to."""
        body, request_id = await self._client._get_json("/engines")
        return AsyncPage.from_body(body, Engine, request_id=request_id)
