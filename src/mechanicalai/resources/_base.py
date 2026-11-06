from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import anyio

from mechanicalai.types._base import BaseModel

if TYPE_CHECKING:
    from mechanicalai._base_client import AsyncAPIClient, SyncAPIClient

M = TypeVar("M", bound=BaseModel)

DOWNLOAD_CHUNK = 1024 * 1024


class NotGiven:
    """The sentinel for an argument left out, where ``None`` is itself a value the API accepts.

    Everywhere in this library a ``None`` argument means "not given" and is left out of the request. The one
    exception is ``projects.update``, where ``name=None`` sends ``null`` to clear the name, so its arguments
    default to ``NOT_GIVEN`` instead.
    """

    _instance: NotGiven | None = None

    def __new__(cls) -> NotGiven:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"


NOT_GIVEN = NotGiven()


def parse_model(model_type: type[M], body: Any, request_id: str | None) -> M:
    model = model_type.model_validate(body)
    model._request_id = request_id
    return model


def iso(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def drop_not_given(body: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if not isinstance(v, NotGiven)}


class SyncAPIResource:
    _client: SyncAPIClient

    def __init__(self, client: SyncAPIClient) -> None:
        self._client = client


class AsyncAPIResource:
    _client: AsyncAPIClient

    def __init__(self, client: AsyncAPIClient) -> None:
        self._client = client


def _temporary_beside(target: Path) -> Path:
    """A fresh, closed temporary file in ``target``'s directory, to be renamed over ``target`` once complete."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False) as tmp:
        return Path(tmp.name)


def download_to(client: SyncAPIClient, url: str, target: Path) -> Path:
    """GET a signed URL and stream the bytes to ``target`` through a temporary file beside it."""
    response = client._external_request("GET", url, follow_redirects=True, stream=True)
    partial = _temporary_beside(target)
    try:
        with open(partial, "wb") as out:
            for chunk in response.iter_bytes(DOWNLOAD_CHUNK):
                out.write(chunk)
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        response.close()
    return target


async def async_download_to(client: AsyncAPIClient, url: str, target: Path) -> Path:
    """The async twin of ``download_to``: the file is written through ``anyio.open_file``."""
    response = await client._external_request("GET", url, follow_redirects=True, stream=True)
    partial = await anyio.to_thread.run_sync(_temporary_beside, target)
    try:
        async with await anyio.open_file(partial, "wb") as out:
            async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK):
                await out.write(chunk)
        await anyio.to_thread.run_sync(os.replace, partial, target)
    except BaseException:
        await anyio.to_thread.run_sync(partial.unlink, True)
        raise
    finally:
        await response.aclose()
    return target


def put_headers(headers: Mapping[str, str], size: int) -> dict[str, str]:
    sent = dict(headers)
    sent.setdefault("Content-Length", str(size))
    return sent
