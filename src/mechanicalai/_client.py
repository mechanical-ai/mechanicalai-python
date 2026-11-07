"""The clients.

``Client`` is bound to one API key and talks to ``https://api.mechanicalai.com/v1``; ``AsyncClient`` is the same
surface with ``await``. Options: ``api_key`` (else ``MECHANICALAI_API_KEY``), ``base_url`` (else
``MECHANICALAI_BASE_URL``), ``timeout`` (60 s, 5 s to connect), ``max_retries`` (2), ``default_headers``.

Arguments: ``None`` means "not given" and is left out of the request, everywhere except ``projects.update``, where
``NOT_GIVEN`` is the sentinel and ``name=None`` sends ``null`` to clear the name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from mechanicalai._base_client import AsyncAPIClient, SyncAPIClient, _ExternalClients
from mechanicalai.resources import (
    Artifacts,
    AsyncArtifacts,
    AsyncEngines,
    AsyncFiles,
    AsyncProjects,
    AsyncRuns,
    AsyncUsageResource,
    Engines,
    Files,
    Projects,
    Runs,
    UsageResource,
)

__all__ = ["AsyncClient", "Client"]


class Client(SyncAPIClient):
    """A synchronous client bound to one API key.

    ::

        client = mechanicalai.Client()                 # key from MECHANICALAI_API_KEY
        client = mechanicalai.Client(api_key="mk_0000000000000000000000000000000000000000")
    """

    engines: Engines
    projects: Projects
    files: Files
    runs: Runs
    artifacts: Artifacts
    usage: UsageResource

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
        _external_clients: _ExternalClients | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
            external_clients=_external_clients,
        )
        self.engines = Engines(self)
        self.projects = Projects(self)
        self.files = Files(self)
        self.runs = Runs(self)
        self.artifacts = Artifacts(self)
        self.usage = UsageResource(self)

    def with_options(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> Client:
        """A client like this one with some options changed, sharing the connection pools."""
        options: dict[str, Any] = self._options()
        if api_key is not None:
            options["api_key"] = api_key
        if base_url is not None:
            options["base_url"] = base_url
        if timeout is not None:
            options["timeout"] = timeout
        if max_retries is not None:
            options["max_retries"] = max_retries
        if default_headers is not None:
            options["default_headers"] = {**options["default_headers"], **default_headers}
        return Client(http_client=self._http, _external_clients=self._externals, **options)

    def __enter__(self) -> Client:
        return self


class AsyncClient(AsyncAPIClient):
    """An asynchronous client bound to one API key; every method is awaited.

    ::

        async with mechanicalai.AsyncClient() as client:
            run = await client.runs.retrieve("run_...")
    """

    engines: AsyncEngines
    projects: AsyncProjects
    files: AsyncFiles
    runs: AsyncRuns
    artifacts: AsyncArtifacts
    usage: AsyncUsageResource

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        _external_clients: _ExternalClients | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
            http_client=http_client,
            external_clients=_external_clients,
        )
        self.engines = AsyncEngines(self)
        self.projects = AsyncProjects(self)
        self.files = AsyncFiles(self)
        self.runs = AsyncRuns(self)
        self.artifacts = AsyncArtifacts(self)
        self.usage = AsyncUsageResource(self)

    def with_options(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> AsyncClient:
        """A client like this one with some options changed, sharing the connection pools."""
        options: dict[str, Any] = self._options()
        if api_key is not None:
            options["api_key"] = api_key
        if base_url is not None:
            options["base_url"] = base_url
        if timeout is not None:
            options["timeout"] = timeout
        if max_retries is not None:
            options["max_retries"] = max_retries
        if default_headers is not None:
            options["default_headers"] = {**options["default_headers"], **default_headers}
        return AsyncClient(http_client=self._http, _external_clients=self._externals, **options)

    async def __aenter__(self) -> AsyncClient:
        return self
