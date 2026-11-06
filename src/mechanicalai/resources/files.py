from __future__ import annotations

import logging
import os
from pathlib import Path

import anyio

from mechanicalai._exceptions import APIStatusError
from mechanicalai._files import FileInput, UploadSource, filename_for, prepare_upload, resolve_destination
from mechanicalai.pagination import AsyncPage, SyncPage
from mechanicalai.resources._base import (
    AsyncAPIResource,
    SyncAPIResource,
    async_download_to,
    download_to,
    parse_model,
    put_headers,
)
from mechanicalai.types.file import DownloadLink, File, UploadUrl

__all__ = ["AsyncFiles", "Files"]

log = logging.getLogger("mechanicalai")


class Files(SyncAPIResource):
    def upload(
        self,
        project_id: str,
        file: FileInput,
        *,
        name: str | None = None,
        content_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> File:
        """Upload a file to a project and return it ``ready``.

        ``file`` is a path, an open binary file (read from its current position), ``bytes`` (then give ``name``)
        or a ``(name, bytes)`` tuple. Up to 64 MB it goes in one multipart request; above that, or when the API
        answers 413, the library asks for an upload URL, PUTs the bytes there with the sha256 digest declared, and
        completes the file.
        """
        source = prepare_upload(file, name=name, content_type=content_type)
        if source.uses_multipart:
            try:
                return self._upload_multipart(project_id, source, idempotency_key)
            except APIStatusError as exc:
                if exc.status_code != 413:
                    raise
                log.info("multipart upload of %s refused with 413; using an upload URL", source.name)
        return self._upload_via_url(project_id, source, idempotency_key)

    def _upload_multipart(self, project_id: str, source: UploadSource, idempotency_key: str | None) -> File:
        # The body is at most MULTIPART_MAX_BYTES and is held in memory so a retried request resends it whole.
        body = source.read_bytes()
        response = self._client._request(
            "POST",
            f"/projects/{project_id}/files",
            files={"file": (source.name, body, source.content_type)},
            data={"name": source.name},
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(File, response.json(), response.headers.get("x-request-id"))

    def _upload_via_url(self, project_id: str, source: UploadSource, idempotency_key: str | None) -> File:
        pending = self.create_upload_url(
            project_id,
            name=source.name,
            size=source.size,
            content_type=source.content_type,
            digest=source.digest(),
            idempotency_key=idempotency_key,
        )
        self._client._external_request(
            "PUT",
            pending.upload_url,
            headers=put_headers(pending.upload_headers, source.size),
            content=source.iter_bytes,
            follow_redirects=False,
        ).close()
        return self.complete(pending.file.id)

    def create_upload_url(
        self,
        project_id: str,
        *,
        name: str,
        size: int,
        content_type: str,
        digest: str | None = None,
        idempotency_key: str | None = None,
    ) -> UploadUrl:
        """A pending file and a signed PUT URL for its bytes (valid 10 minutes); then ``complete`` it."""
        body, request_id = self._client._post_json(
            f"/projects/{project_id}/files/upload-url",
            json={"name": name, "size": size, "content_type": content_type, "digest": digest},
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(UploadUrl, body, request_id)

    def complete(self, file_id: str) -> File:
        """Mark a direct upload complete; the file turns ``ready``."""
        body, request_id = self._client._post_json(f"/files/{file_id}/complete")
        return parse_model(File, body, request_id)

    def retrieve(self, file_id: str) -> File:
        body, request_id = self._client._get_json(f"/files/{file_id}")
        return parse_model(File, body, request_id)

    def list(self, project_id: str, *, limit: int | None = None, after: str | None = None) -> SyncPage[File]:
        body, request_id = self._client._get_json(
            f"/projects/{project_id}/files", params={"limit": limit, "after": after}
        )
        return SyncPage.from_body(
            body,
            File,
            request_id=request_id,
            fetch_next=lambda cursor: self.list(project_id, limit=limit, after=cursor),
        )

    def download_url(self, file_id: str) -> DownloadLink:
        """A download link that lives 10 minutes. Ask again when it expires."""
        body, request_id = self._client._get_json(f"/files/{file_id}/download")
        return parse_model(DownloadLink, body, request_id)

    def download(self, file: str | File, dest: str | os.PathLike[str]) -> Path:
        """Fetch a fresh link and stream the file's bytes to ``dest`` (a directory, or the path to write)."""
        record = self.retrieve(file) if isinstance(file, str) else file
        link = self.download_url(record.id)
        target = resolve_destination(dest, filename_for(record.name, None))
        return download_to(self._client, link.url, target)

    def delete(self, file_id: str) -> None:
        """Delete a file. One attached to a live run is refused (409 ``file_in_use``)."""
        self._client._delete(f"/files/{file_id}")


class AsyncFiles(AsyncAPIResource):
    async def upload(
        self,
        project_id: str,
        file: FileInput,
        *,
        name: str | None = None,
        content_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> File:
        """Upload a file to a project and return it ``ready``; see ``Files.upload``. Hashing and reads run in a
        worker thread, so the event loop never blocks on the disk."""
        source = await anyio.to_thread.run_sync(lambda: prepare_upload(file, name=name, content_type=content_type))
        if source.uses_multipart:
            try:
                return await self._upload_multipart(project_id, source, idempotency_key)
            except APIStatusError as exc:
                if exc.status_code != 413:
                    raise
                log.info("multipart upload of %s refused with 413; using an upload URL", source.name)
        return await self._upload_via_url(project_id, source, idempotency_key)

    async def _upload_multipart(self, project_id: str, source: UploadSource, idempotency_key: str | None) -> File:
        body = await anyio.to_thread.run_sync(source.read_bytes)
        response = await self._client._request(
            "POST",
            f"/projects/{project_id}/files",
            files={"file": (source.name, body, source.content_type)},
            data={"name": source.name},
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(File, response.json(), response.headers.get("x-request-id"))

    async def _upload_via_url(self, project_id: str, source: UploadSource, idempotency_key: str | None) -> File:
        pending = await self.create_upload_url(
            project_id,
            name=source.name,
            size=source.size,
            content_type=source.content_type,
            digest=await source.adigest(),
            idempotency_key=idempotency_key,
        )
        response = await self._client._external_request(
            "PUT",
            pending.upload_url,
            headers=put_headers(pending.upload_headers, source.size),
            content=source.aiter_bytes,
            follow_redirects=False,
        )
        await response.aclose()
        return await self.complete(pending.file.id)

    async def create_upload_url(
        self,
        project_id: str,
        *,
        name: str,
        size: int,
        content_type: str,
        digest: str | None = None,
        idempotency_key: str | None = None,
    ) -> UploadUrl:
        """A pending file and a signed PUT URL for its bytes (valid 10 minutes); then ``complete`` it."""
        body, request_id = await self._client._post_json(
            f"/projects/{project_id}/files/upload-url",
            json={"name": name, "size": size, "content_type": content_type, "digest": digest},
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(UploadUrl, body, request_id)

    async def complete(self, file_id: str) -> File:
        """Mark a direct upload complete; the file turns ``ready``."""
        body, request_id = await self._client._post_json(f"/files/{file_id}/complete")
        return parse_model(File, body, request_id)

    async def retrieve(self, file_id: str) -> File:
        body, request_id = await self._client._get_json(f"/files/{file_id}")
        return parse_model(File, body, request_id)

    async def list(self, project_id: str, *, limit: int | None = None, after: str | None = None) -> AsyncPage[File]:
        body, request_id = await self._client._get_json(
            f"/projects/{project_id}/files", params={"limit": limit, "after": after}
        )
        return AsyncPage.from_body(
            body,
            File,
            request_id=request_id,
            fetch_next=lambda cursor: self.list(project_id, limit=limit, after=cursor),
        )

    async def download_url(self, file_id: str) -> DownloadLink:
        """A download link that lives 10 minutes. Ask again when it expires."""
        body, request_id = await self._client._get_json(f"/files/{file_id}/download")
        return parse_model(DownloadLink, body, request_id)

    async def download(self, file: str | File, dest: str | os.PathLike[str]) -> Path:
        """Fetch a fresh link and stream the file's bytes to ``dest`` (a directory, or the path to write)."""
        record = await self.retrieve(file) if isinstance(file, str) else file
        link = await self.download_url(record.id)
        target = resolve_destination(dest, filename_for(record.name, None))
        return await async_download_to(self._client, link.url, target)

    async def delete(self, file_id: str) -> None:
        """Delete a file. One attached to a live run is refused (409 ``file_in_use``)."""
        await self._client._delete(f"/files/{file_id}")
