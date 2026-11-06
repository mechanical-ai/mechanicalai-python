"""Server-sent events for ``GET /runs/{id}/events/stream``.

The stream sends ``id: <seq>``, ``event: <kind>`` and a JSON ``data:`` line per event, a comment line every 15 s as
keep-alive, and closes after the terminal ``status`` event. The reader keeps the last ``id`` and, when the connection
drops before a terminal status, reconnects with ``Last-Event-ID`` set to exactly that id. The wait before a reconnect
is the stream's ``retry:`` field when it sent one (bounded), else 1 s doubling to 8 s; the drop counter resets after
every received event, so only ``max_reconnects`` drops in a row without an event give up.

An event whose ``kind`` or ``status`` the library does not know is passed through unchanged; the stream ends only on
the five known terminal statuses.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anyio
import httpx

from mechanicalai._exceptions import APIConnectionError, APIStatusError
from mechanicalai.types.run import TERMINAL_RUN_STATUSES
from mechanicalai.types.run_event import RunEvent

if TYPE_CHECKING:
    from mechanicalai._base_client import AsyncAPIClient, SyncAPIClient

__all__ = ["AsyncRunEventStream", "RunEventStream", "SSEDecoder", "ServerSentEvent", "parse_sse"]

log = logging.getLogger("mechanicalai")

DEFAULT_MAX_RECONNECTS = 5
RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 8.0
MIN_SSE_RETRY_S = 0.1
MAX_SSE_RETRY_S = 30.0


@dataclass
class ServerSentEvent:
    """One event as the wire carries it."""

    id: str | None = None
    event: str | None = None
    data: list[str] = field(default_factory=list)
    retry: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.data)

    def json(self) -> Any:
        return json.loads(self.text)

    def _is_empty(self) -> bool:
        return not self.data and self.id is None and self.event is None and self.retry is None


class SSEDecoder:
    """An incremental text/event-stream decoder (the WHATWG algorithm; comments are skipped)."""

    def __init__(self) -> None:
        self._current = ServerSentEvent()

    def feed(self, raw: str) -> ServerSentEvent | None:
        """Feed one line; the event it completes, if any."""
        line = raw.rstrip("\r\n")
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        current = self._current
        if name == "data":
            current.data.append(value)
        elif name == "event":
            current.event = value
        elif name == "id":
            if "\0" not in value:
                current.id = value
        elif name == "retry":
            try:
                current.retry = int(value)
            except ValueError:
                pass
        return None

    def flush(self) -> ServerSentEvent | None:
        """The event left unterminated when the stream ends, if any."""
        return self._dispatch()

    def _dispatch(self) -> ServerSentEvent | None:
        current = self._current
        self._current = ServerSentEvent()
        return None if current._is_empty() else current


def parse_sse(lines: Iterable[str]) -> Iterator[ServerSentEvent]:
    """The events of a whole text/event-stream, from its lines."""
    decoder = SSEDecoder()
    for line in lines:
        event = decoder.feed(line)
        if event is not None:
            yield event
    tail = decoder.flush()
    if tail is not None:
        yield tail


def _event_from(sse: ServerSentEvent) -> RunEvent | None:
    if not sse.data:
        return None
    try:
        payload = sse.json()
    except ValueError:
        log.warning("skipping an event with a body that is not JSON: %r", sse.text[:200])
        return None
    if not isinstance(payload, dict):
        return None
    if sse.event and "kind" not in payload:
        payload["kind"] = sse.event
    return RunEvent.model_validate(payload)


def _is_terminal(event: RunEvent) -> bool:
    return event.kind == "status" and event.status in TERMINAL_RUN_STATUSES


class _StreamState:
    def __init__(self, run_id: str, max_reconnects: int) -> None:
        self.run_id = run_id
        self.max_reconnects = max_reconnects
        self.last_event_id: str | None = None
        self.reconnects = 0
        self.finished = False
        self.retry_s: float | None = None

    @property
    def path(self) -> str:
        return f"/runs/{self.run_id}/events/stream"

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-store"}
        if self.last_event_id is not None:
            headers["Last-Event-ID"] = self.last_event_id
        return headers

    def take(self, sse: ServerSentEvent) -> RunEvent | None:
        if sse.retry is not None:
            self.retry_s = min(max(sse.retry / 1000.0, MIN_SSE_RETRY_S), MAX_SSE_RETRY_S)
        if sse.id is not None:
            self.last_event_id = sse.id
        event = _event_from(sse)
        if event is not None:
            self.reconnects = 0
            if _is_terminal(event):
                self.finished = True
        return event

    def note_drop(self, reason: str) -> float:
        """Count a drop and return the seconds to wait before reconnecting; raise once too many came in a row."""
        if self.reconnects >= self.max_reconnects:
            raise APIConnectionError(
                f"The event stream of {self.run_id} dropped ({reason}) and did not come back after "
                f"{self.max_reconnects} reconnects; last event id {self.last_event_id!r}."
            )
        self.reconnects += 1
        if self.retry_s is not None:
            delay = self.retry_s
        else:
            delay = min(RECONNECT_DELAY * (2.0 ** (self.reconnects - 1)), MAX_RECONNECT_DELAY)
        log.info(
            "event stream dropped (%s); reconnect %d/%d in %.1fs from %r",
            reason,
            self.reconnects,
            self.max_reconnects,
            delay,
            self.last_event_id,
        )
        return delay


class RunEventStream:
    """Iterate to receive a run's events until the terminal ``status``; ``last_event_id`` is the resume point."""

    def __init__(self, client: SyncAPIClient, run_id: str, *, max_reconnects: int = DEFAULT_MAX_RECONNECTS) -> None:
        self._client = client
        self._state = _StreamState(run_id, max_reconnects)

    @property
    def last_event_id(self) -> str | None:
        return self._state.last_event_id

    @property
    def run_id(self) -> str:
        return self._state.run_id

    def __iter__(self) -> Iterator[RunEvent]:
        state = self._state
        while not state.finished:
            try:
                with self._client._stream("GET", state.path, headers=state.headers()) as response:
                    for sse in parse_sse(response.iter_lines()):
                        event = state.take(sse)
                        if event is not None:
                            yield event
                        if state.finished:
                            return
            except APIStatusError:
                raise
            except (httpx.TransportError, APIConnectionError) as exc:
                delay = state.note_drop(type(exc).__name__)
            else:
                delay = state.note_drop("closed before the terminal status")
            time.sleep(delay)


class AsyncRunEventStream:
    """``async for`` to receive a run's events until the terminal ``status``; ``last_event_id`` is the resume point."""

    def __init__(self, client: AsyncAPIClient, run_id: str, *, max_reconnects: int = DEFAULT_MAX_RECONNECTS) -> None:
        self._client = client
        self._state = _StreamState(run_id, max_reconnects)

    @property
    def last_event_id(self) -> str | None:
        return self._state.last_event_id

    @property
    def run_id(self) -> str:
        return self._state.run_id

    async def __aiter__(self) -> AsyncIterator[RunEvent]:
        state = self._state
        while not state.finished:
            try:
                async with self._client._stream("GET", state.path, headers=state.headers()) as response:
                    decoder = SSEDecoder()
                    async for line in response.aiter_lines():
                        sse = decoder.feed(line)
                        if sse is None:
                            continue
                        event = state.take(sse)
                        if event is not None:
                            yield event
                        if state.finished:
                            return
                    tail = decoder.flush()
                    if tail is not None:
                        event = state.take(tail)
                        if event is not None:
                            yield event
                        if state.finished:
                            return
            except APIStatusError:
                raise
            except (httpx.TransportError, APIConnectionError) as exc:
                delay = state.note_drop(type(exc).__name__)
            else:
                delay = state.note_drop("closed before the terminal status")
            await anyio.sleep(delay)
