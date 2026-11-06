from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import anyio

from mechanicalai._exceptions import WaitTimeoutError
from mechanicalai._streaming import DEFAULT_MAX_RECONNECTS, AsyncRunEventStream, RunEventStream
from mechanicalai.pagination import AsyncPage, SyncPage
from mechanicalai.resources._base import AsyncAPIResource, SyncAPIResource, parse_model
from mechanicalai.types.project import Metadata
from mechanicalai.types.run import Language, Run, Units
from mechanicalai.types.run_event import RunEvent

__all__ = ["AsyncRunEvents", "AsyncRuns", "RunEvents", "Runs"]

DEFAULT_POLL_INTERVAL = 2.0


def _run_body(
    *,
    engine: str,
    problem: str,
    budget_cents: int,
    files: Sequence[str] | None,
    instructions: str | None,
    units: Units | None,
    language: Language | None,
    allow_web: bool | None,
    end_user: str | None,
    metadata: Metadata | None,
    webhook_url: str | None,
    project_id: str | None = None,
) -> dict[str, Any]:
    return {
        "engine": engine,
        "problem": problem,
        "budget_cents": budget_cents,
        "files": list(files) if files is not None else None,
        "instructions": instructions,
        "units": units,
        "language": language,
        "allow_web": allow_web,
        "end_user": end_user,
        "metadata": metadata,
        "webhook_url": webhook_url,
        "project_id": project_id,
    }


class RunEvents(SyncAPIResource):
    def list(self, run_id: str, *, after: str | None = None, limit: int | None = None) -> SyncPage[RunEvent]:
        """A run's progress events, ascending by sequence; ``after`` is the previous page's ``next_cursor``."""
        body, request_id = self._client._get_json(f"/runs/{run_id}/events", params={"after": after, "limit": limit})
        return SyncPage.from_body(
            body,
            RunEvent,
            request_id=request_id,
            fetch_next=lambda cursor: self.list(run_id, after=cursor, limit=limit),
        )


class Runs(SyncAPIResource):
    events: RunEvents

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.events = RunEvents(client)

    def create(
        self,
        project_id: str,
        *,
        engine: str,
        problem: str,
        budget_cents: int,
        files: Sequence[str] | None = None,
        instructions: str | None = None,
        units: Units | None = None,
        language: Language | None = None,
        allow_web: bool | None = None,
        end_user: str | None = None,
        metadata: Metadata | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        """Start a run on a project; it comes back ``queued``.

        ``engine`` is ``"mekint"`` (``engine_version`` reports the snapshot that ran). ``budget_cents`` (at least
        100) is the most the run may charge. ``files`` are ids of ``ready`` files in this project. One run at a time
        per project: a second one answers 409 ``project_busy`` (``ProjectBusyError``).
        """
        body, request_id = self._client._post_json(
            f"/projects/{project_id}/runs",
            json=_run_body(
                engine=engine,
                problem=problem,
                budget_cents=budget_cents,
                files=files,
                instructions=instructions,
                units=units,
                language=language,
                allow_web=allow_web,
                end_user=end_user,
                metadata=metadata,
                webhook_url=webhook_url,
            ),
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(Run, body, request_id)

    def create_in_new_project(
        self,
        *,
        engine: str,
        problem: str,
        budget_cents: int,
        project_id: str | None = None,
        files: Sequence[str] | None = None,
        instructions: str | None = None,
        units: Units | None = None,
        language: Language | None = None,
        allow_web: bool | None = None,
        end_user: str | None = None,
        metadata: Metadata | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        """Start a run, creating a project when ``project_id`` is None; the run's ``project_id`` names it."""
        body, request_id = self._client._post_json(
            "/runs",
            json=_run_body(
                engine=engine,
                problem=problem,
                budget_cents=budget_cents,
                files=files,
                instructions=instructions,
                units=units,
                language=language,
                allow_web=allow_web,
                end_user=end_user,
                metadata=metadata,
                webhook_url=webhook_url,
                project_id=project_id,
            ),
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(Run, body, request_id)

    def retrieve(self, run_id: str) -> Run:
        body, request_id = self._client._get_json(f"/runs/{run_id}")
        return parse_model(Run, body, request_id)

    def list(self, project_id: str, *, limit: int | None = None, after: str | None = None) -> SyncPage[Run]:
        """A project's runs, newest first."""
        body, request_id = self._client._get_json(
            f"/projects/{project_id}/runs", params={"limit": limit, "after": after}
        )
        return SyncPage.from_body(
            body, Run, request_id=request_id, fetch_next=lambda cursor: self.list(project_id, limit=limit, after=cursor)
        )

    def cancel(self, run_id: str) -> Run:
        """Stop the run at once: a queued run is cancelled free; a running one is marked cancelled, its live tool run
        killed, and charged for the window it ran."""
        body, request_id = self._client._post_json(f"/runs/{run_id}/cancel")
        return parse_model(Run, body, request_id)

    def reply(
        self,
        run_id: str,
        *,
        text: str,
        files: Sequence[str] | None = None,
        budget_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        """Answer a ``needs_input`` run's question, once: a new run on the same project with ``parent_run_id`` set.

        The new run inherits the parent's engine, units, language, allow_web, end_user, metadata and webhook_url;
        ``files`` are added to the parent's and ``budget_cents`` defaults to the parent's.
        """
        body, request_id = self._client._post_json(
            f"/runs/{run_id}/reply",
            json={"text": text, "files": list(files) if files is not None else None, "budget_cents": budget_cents},
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(Run, body, request_id)

    def wait(self, run_id: str, *, poll_interval: float = DEFAULT_POLL_INTERVAL, timeout: float | None = None) -> Run:
        """Poll until the run is terminal and return it. ``WaitTimeoutError`` after ``timeout`` seconds, if given."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            run = self.retrieve(run_id)
            if run.is_terminal:
                return run
            if deadline is not None and time.monotonic() >= deadline:
                raise WaitTimeoutError(f"Run {run_id} is still {run.status} after {timeout} s.")
            time.sleep(poll_interval)

    def stream(self, run_id: str, *, max_reconnects: int = DEFAULT_MAX_RECONNECTS) -> RunEventStream:
        """The run's events as they happen, until its terminal status; reconnects on a dropped connection."""
        return RunEventStream(self._client, run_id, max_reconnects=max_reconnects)


class AsyncRunEvents(AsyncAPIResource):
    async def list(self, run_id: str, *, after: str | None = None, limit: int | None = None) -> AsyncPage[RunEvent]:
        """A run's progress events, ascending by sequence; ``after`` is the previous page's ``next_cursor``."""
        body, request_id = await self._client._get_json(
            f"/runs/{run_id}/events", params={"after": after, "limit": limit}
        )
        return AsyncPage.from_body(
            body,
            RunEvent,
            request_id=request_id,
            fetch_next=lambda cursor: self.list(run_id, after=cursor, limit=limit),
        )


class AsyncRuns(AsyncAPIResource):
    events: AsyncRunEvents

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.events = AsyncRunEvents(client)

    async def create(
        self,
        project_id: str,
        *,
        engine: str,
        problem: str,
        budget_cents: int,
        files: Sequence[str] | None = None,
        instructions: str | None = None,
        units: Units | None = None,
        language: Language | None = None,
        allow_web: bool | None = None,
        end_user: str | None = None,
        metadata: Metadata | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        """Start a run on a project; it comes back ``queued``. See ``Runs.create``."""
        body, request_id = await self._client._post_json(
            f"/projects/{project_id}/runs",
            json=_run_body(
                engine=engine,
                problem=problem,
                budget_cents=budget_cents,
                files=files,
                instructions=instructions,
                units=units,
                language=language,
                allow_web=allow_web,
                end_user=end_user,
                metadata=metadata,
                webhook_url=webhook_url,
            ),
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(Run, body, request_id)

    async def create_in_new_project(
        self,
        *,
        engine: str,
        problem: str,
        budget_cents: int,
        project_id: str | None = None,
        files: Sequence[str] | None = None,
        instructions: str | None = None,
        units: Units | None = None,
        language: Language | None = None,
        allow_web: bool | None = None,
        end_user: str | None = None,
        metadata: Metadata | None = None,
        webhook_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        """Start a run, creating a project when ``project_id`` is None; the run's ``project_id`` names it."""
        body, request_id = await self._client._post_json(
            "/runs",
            json=_run_body(
                engine=engine,
                problem=problem,
                budget_cents=budget_cents,
                files=files,
                instructions=instructions,
                units=units,
                language=language,
                allow_web=allow_web,
                end_user=end_user,
                metadata=metadata,
                webhook_url=webhook_url,
                project_id=project_id,
            ),
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(Run, body, request_id)

    async def retrieve(self, run_id: str) -> Run:
        body, request_id = await self._client._get_json(f"/runs/{run_id}")
        return parse_model(Run, body, request_id)

    async def list(self, project_id: str, *, limit: int | None = None, after: str | None = None) -> AsyncPage[Run]:
        """A project's runs, newest first."""
        body, request_id = await self._client._get_json(
            f"/projects/{project_id}/runs", params={"limit": limit, "after": after}
        )
        return AsyncPage.from_body(
            body, Run, request_id=request_id, fetch_next=lambda cursor: self.list(project_id, limit=limit, after=cursor)
        )

    async def cancel(self, run_id: str) -> Run:
        """Stop the run at once: a queued run is cancelled free; a running one is marked cancelled, its live tool run
        killed, and charged for the window it ran."""
        body, request_id = await self._client._post_json(f"/runs/{run_id}/cancel")
        return parse_model(Run, body, request_id)

    async def reply(
        self,
        run_id: str,
        *,
        text: str,
        files: Sequence[str] | None = None,
        budget_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        """Answer a ``needs_input`` run's question, once: a new run on the same project with ``parent_run_id`` set.

        The new run inherits the parent's engine, units, language, allow_web, end_user, metadata and webhook_url;
        ``files`` are added to the parent's and ``budget_cents`` defaults to the parent's.
        """
        body, request_id = await self._client._post_json(
            f"/runs/{run_id}/reply",
            json={"text": text, "files": list(files) if files is not None else None, "budget_cents": budget_cents},
            idempotent=True,
            idempotency_key=idempotency_key,
        )
        return parse_model(Run, body, request_id)

    async def wait(
        self, run_id: str, *, poll_interval: float = DEFAULT_POLL_INTERVAL, timeout: float | None = None
    ) -> Run:
        """Poll until the run is terminal and return it. ``WaitTimeoutError`` after ``timeout`` seconds, if given."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            run = await self.retrieve(run_id)
            if run.is_terminal:
                return run
            if deadline is not None and time.monotonic() >= deadline:
                raise WaitTimeoutError(f"Run {run_id} is still {run.status} after {timeout} s.")
            await anyio.sleep(poll_interval)

    def stream(self, run_id: str, *, max_reconnects: int = DEFAULT_MAX_RECONNECTS) -> AsyncRunEventStream:
        """The run's events as they happen, until its terminal status; reconnects on a dropped connection."""
        return AsyncRunEventStream(self._client, run_id, max_reconnects=max_reconnects)
