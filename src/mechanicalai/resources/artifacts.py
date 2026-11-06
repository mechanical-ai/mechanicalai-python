from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import anyio
import httpx

from mechanicalai._exceptions import WaitTimeoutError, parse_retry_after
from mechanicalai._files import filename_for, resolve_destination
from mechanicalai.pagination import AsyncPage, SyncPage
from mechanicalai.resources._base import AsyncAPIResource, SyncAPIResource, async_download_to, download_to, parse_model
from mechanicalai.types.artifact import Artifact, ArtifactKind
from mechanicalai.types.file import DownloadLink

__all__ = ["Artifacts", "AsyncArtifacts"]

DEFAULT_PREPARE_TIMEOUT = 300.0
"""How long ``download_url`` waits while a format is composed (202 ``preparing``) before giving up."""

MAX_PREPARE_RETRY_AFTER = 30.0


def _preparing_delay(response: httpx.Response, body: Any) -> float:
    """The wait a 202 asks for: the ``Retry-After`` header first, the body's ``retry_after`` second, else 5 s."""
    header = parse_retry_after(response.headers.get("retry-after"))
    if header is not None and header > 0:
        return min(header, MAX_PREPARE_RETRY_AFTER)
    retry_after = body.get("retry_after") if isinstance(body, dict) else None
    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool) and retry_after > 0:
        return min(float(retry_after), MAX_PREPARE_RETRY_AFTER)
    return 5.0


class Artifacts(SyncAPIResource):
    def list_for_run(self, run_id: str, *, kind: ArtifactKind | None = None) -> SyncPage[Artifact]:
        """What a run produced. The route is not paged (a run produces a handful); should the API ever answer
        ``has_more``, iteration continues through the project's artifacts filtered to this run."""
        body, request_id = self._client._get_json(f"/runs/{run_id}/artifacts", params={"kind": kind})
        page = SyncPage.from_body(body, Artifact, request_id=request_id)
        if page.has_more and page.data:
            project_id = page.data[-1].project_id
            page._fetch_next = lambda cursor: self.list_for_project(project_id, kind=kind, run_id=run_id, after=cursor)
        return page

    def list_for_project(
        self,
        project_id: str,
        *,
        kind: ArtifactKind | None = None,
        run_id: str | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> SyncPage[Artifact]:
        """Everything a project's runs produced, newest first."""
        body, request_id = self._client._get_json(
            f"/projects/{project_id}/artifacts", params={"kind": kind, "run_id": run_id, "limit": limit, "after": after}
        )
        return SyncPage.from_body(
            body,
            Artifact,
            request_id=request_id,
            fetch_next=lambda cursor: self.list_for_project(
                project_id, kind=kind, run_id=run_id, limit=limit, after=cursor
            ),
        )

    def retrieve(self, artifact_id: str) -> Artifact:
        body, request_id = self._client._get_json(f"/artifacts/{artifact_id}")
        return parse_model(Artifact, body, request_id)

    def download_url(
        self, artifact_id: str, *, format: str | None = None, timeout: float | None = DEFAULT_PREPARE_TIMEOUT
    ) -> DownloadLink:
        """A download link in one of the artifact's ``formats`` (its ``default_format`` when none); lives 10 minutes.

        A model or table format is composed on first request (then kept): the API answers 202 ``preparing`` with
        ``Retry-After`` until it is ready, and this waits accordingly, up to ``timeout`` seconds
        (``WaitTimeoutError`` after that; ``None`` waits without limit).
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            response = self._client._request("GET", f"/artifacts/{artifact_id}/download", params={"format": format})
            body = response.json()
            if response.status_code != 202:
                return parse_model(DownloadLink, body, response.headers.get("x-request-id"))
            delay = _preparing_delay(response, body)
            if deadline is not None and time.monotonic() + delay > deadline:
                raise WaitTimeoutError(f"Artifact {artifact_id} is still being composed after {timeout} s.")
            time.sleep(delay)

    def download(
        self,
        artifact: str | Artifact,
        dest: str | os.PathLike[str],
        *,
        format: str | None = None,
        timeout: float | None = DEFAULT_PREPARE_TIMEOUT,
    ) -> Path:
        """Fetch a fresh link (waiting while the format is composed) and stream the bytes to ``dest``.

        ``dest`` is a directory (the file is ``<name>.<format>``, the name made safe for any file system) or the
        path to write. The bytes land in a temporary file beside the target and are moved into place when complete.
        Returns the path written.
        """
        record = self.retrieve(artifact) if isinstance(artifact, str) else artifact
        link = self.download_url(record.id, format=format, timeout=timeout)
        target = resolve_destination(dest, filename_for(record.name, link.format or format or record.default_format))
        return download_to(self._client, link.url, target)

    def delete(self, artifact_id: str) -> None:
        """Delete an artifact; frees its storage. A later run no longer sees it."""
        self._client._delete(f"/artifacts/{artifact_id}")


class AsyncArtifacts(AsyncAPIResource):
    async def list_for_run(self, run_id: str, *, kind: ArtifactKind | None = None) -> AsyncPage[Artifact]:
        """What a run produced; see ``Artifacts.list_for_run``."""
        body, request_id = await self._client._get_json(f"/runs/{run_id}/artifacts", params={"kind": kind})
        page = AsyncPage.from_body(body, Artifact, request_id=request_id)
        if page.has_more and page.data:
            project_id = page.data[-1].project_id
            page._fetch_next = lambda cursor: self.list_for_project(project_id, kind=kind, run_id=run_id, after=cursor)
        return page

    async def list_for_project(
        self,
        project_id: str,
        *,
        kind: ArtifactKind | None = None,
        run_id: str | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> AsyncPage[Artifact]:
        """Everything a project's runs produced, newest first."""
        body, request_id = await self._client._get_json(
            f"/projects/{project_id}/artifacts", params={"kind": kind, "run_id": run_id, "limit": limit, "after": after}
        )
        return AsyncPage.from_body(
            body,
            Artifact,
            request_id=request_id,
            fetch_next=lambda cursor: self.list_for_project(
                project_id, kind=kind, run_id=run_id, limit=limit, after=cursor
            ),
        )

    async def retrieve(self, artifact_id: str) -> Artifact:
        body, request_id = await self._client._get_json(f"/artifacts/{artifact_id}")
        return parse_model(Artifact, body, request_id)

    async def download_url(
        self, artifact_id: str, *, format: str | None = None, timeout: float | None = DEFAULT_PREPARE_TIMEOUT
    ) -> DownloadLink:
        """A download link in one of the artifact's ``formats``; waits while the format is composed (202).

        See ``Artifacts.download_url``.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            response = await self._client._request(
                "GET", f"/artifacts/{artifact_id}/download", params={"format": format}
            )
            body = response.json()
            if response.status_code != 202:
                return parse_model(DownloadLink, body, response.headers.get("x-request-id"))
            delay = _preparing_delay(response, body)
            if deadline is not None and time.monotonic() + delay > deadline:
                raise WaitTimeoutError(f"Artifact {artifact_id} is still being composed after {timeout} s.")
            await anyio.sleep(delay)

    async def download(
        self,
        artifact: str | Artifact,
        dest: str | os.PathLike[str],
        *,
        format: str | None = None,
        timeout: float | None = DEFAULT_PREPARE_TIMEOUT,
    ) -> Path:
        """Fetch a fresh link (waiting while the format is composed) and stream the bytes to ``dest``; see
        ``Artifacts.download``."""
        record = await self.retrieve(artifact) if isinstance(artifact, str) else artifact
        link = await self.download_url(record.id, format=format, timeout=timeout)
        target = resolve_destination(dest, filename_for(record.name, link.format or format or record.default_format))
        return await async_download_to(self._client, link.url, target)

    async def delete(self, artifact_id: str) -> None:
        """Delete an artifact; frees its storage. A later run no longer sees it."""
        await self._client._delete(f"/artifacts/{artifact_id}")
