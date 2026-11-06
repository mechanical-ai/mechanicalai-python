from __future__ import annotations

from mechanicalai.pagination import AsyncPage, SyncPage
from mechanicalai.resources._base import (
    NOT_GIVEN,
    AsyncAPIResource,
    NotGiven,
    SyncAPIResource,
    drop_not_given,
    parse_model,
)
from mechanicalai.types.project import Metadata, Project

__all__ = ["AsyncProjects", "Projects"]


class Projects(SyncAPIResource):
    def create(
        self,
        *,
        name: str | None = None,
        metadata: Metadata | None = None,
        idempotency_key: str | None = None,
    ) -> Project:
        """Create a project. An unnamed one is named by the engine on its first run."""
        body, request_id = self._client._post_json(
            "/projects", json={"name": name, "metadata": metadata}, idempotent=True, idempotency_key=idempotency_key
        )
        return parse_model(Project, body, request_id)

    def retrieve(self, project_id: str) -> Project:
        body, request_id = self._client._get_json(f"/projects/{project_id}")
        return parse_model(Project, body, request_id)

    def list(self, *, limit: int | None = None, after: str | None = None) -> SyncPage[Project]:
        """Projects, newest first. Iterate the page to walk every project."""
        body, request_id = self._client._get_json("/projects", params={"limit": limit, "after": after})
        return SyncPage.from_body(
            body, Project, request_id=request_id, fetch_next=lambda cursor: self.list(limit=limit, after=cursor)
        )

    def update(
        self,
        project_id: str,
        *,
        name: str | None | NotGiven = NOT_GIVEN,
        metadata: Metadata | NotGiven = NOT_GIVEN,
    ) -> Project:
        """Rename a project (``name=None`` clears it) or replace its metadata."""
        body, request_id = self._client._patch_json(
            f"/projects/{project_id}", json=drop_not_given({"name": name, "metadata": metadata})
        )
        return parse_model(Project, body, request_id)

    def delete(self, project_id: str) -> None:
        """Delete a project and everything in it: files, runs and artifacts. Immediate and final."""
        self._client._delete(f"/projects/{project_id}")


class AsyncProjects(AsyncAPIResource):
    async def create(
        self,
        *,
        name: str | None = None,
        metadata: Metadata | None = None,
        idempotency_key: str | None = None,
    ) -> Project:
        """Create a project. An unnamed one is named by the engine on its first run."""
        body, request_id = await self._client._post_json(
            "/projects", json={"name": name, "metadata": metadata}, idempotent=True, idempotency_key=idempotency_key
        )
        return parse_model(Project, body, request_id)

    async def retrieve(self, project_id: str) -> Project:
        body, request_id = await self._client._get_json(f"/projects/{project_id}")
        return parse_model(Project, body, request_id)

    async def list(self, *, limit: int | None = None, after: str | None = None) -> AsyncPage[Project]:
        """Projects, newest first. ``async for`` over the page walks every project."""
        body, request_id = await self._client._get_json("/projects", params={"limit": limit, "after": after})
        return AsyncPage.from_body(
            body, Project, request_id=request_id, fetch_next=lambda cursor: self.list(limit=limit, after=cursor)
        )

    async def update(
        self,
        project_id: str,
        *,
        name: str | None | NotGiven = NOT_GIVEN,
        metadata: Metadata | NotGiven = NOT_GIVEN,
    ) -> Project:
        """Rename a project (``name=None`` clears it) or replace its metadata."""
        body, request_id = await self._client._patch_json(
            f"/projects/{project_id}", json=drop_not_given({"name": name, "metadata": metadata})
        )
        return parse_model(Project, body, request_id)

    async def delete(self, project_id: str) -> None:
        """Delete a project and everything in it: files, runs and artifacts. Immediate and final."""
        await self._client._delete(f"/projects/{project_id}")
