from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from mechanicalai import APIConnectionError, AsyncClient, Client, NotFoundError
from mechanicalai._streaming import SSEDecoder, parse_sse
from tests.conftest import EVENTS, error_body, sse_response, sse_text


def test_parse_sse_follows_the_wire_format() -> None:
    text = (
        'id: 3\r\nevent: progress\r\ndata: {"a": 1,\r\ndata:  "b": 2}\r\n\r\n'
        ": keep-alive\n\n"
        "data:no-space\n\n"
        "retry: 5000\nid: 9\nevent: status\ndata: {}\n"
    )
    events = list(parse_sse(text.splitlines(keepends=True)))
    assert len(events) == 3
    assert events[0].id == "3" and events[0].event == "progress"
    assert json.loads(events[0].text) == {"a": 1, "b": 2}
    assert events[1].data == ["no-space"] and events[1].id is None
    assert events[2].id == "9" and events[2].retry == 5000 and events[2].event == "status"


def test_decoder_ignores_ids_with_nul_and_bad_retries() -> None:
    decoder = SSEDecoder()
    assert decoder.feed("id: a\0b") is None
    assert decoder.feed("retry: soon") is None
    assert decoder.feed("data: x") is None
    event = decoder.feed("")
    assert event is not None and event.id is None and event.retry is None and event.data == ["x"]
    assert decoder.flush() is None


def test_stream_yields_events_until_the_terminal_status(api: respx.MockRouter, client: Client) -> None:
    route = api.get("/runs/run_1/events/stream").mock(return_value=sse_response(sse_text(EVENTS)))
    stream = client.runs.stream("run_1")
    events = list(stream)
    assert [e.kind for e in events] == ["status", "phase", "progress", "task", "status"]
    assert events[2].text == "Reading the plate and the load cases."
    assert events[3].percent == 65 and events[3].eta_s == 50
    assert events[-1].status == "succeeded"
    assert stream.last_event_id == "5"
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["accept"] == "text/event-stream"
    assert "last-event-id" not in request.headers


def test_stream_resumes_with_last_event_id_after_a_clean_drop(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        last = request.headers.get("last-event-id")
        if last is None:
            return sse_response(sse_text(EVENTS[:3]))
        assert last == "3"
        return sse_response(sse_text(EVENTS[3:]))

    route = api.get("/runs/run_1/events/stream").mock(side_effect=answer)
    events = list(client.runs.stream("run_1"))
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert route.call_count == 2
    assert route.calls[1].request.headers["last-event-id"] == "3"
    assert sleeps == [1.0]


class _DroppingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks
        raise httpx.ReadError("connection reset")


def test_stream_resumes_after_a_transport_error_mid_stream(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    dropping = httpx.Response(
        200, stream=_DroppingStream([sse_text(EVENTS[:2]).encode()]), headers={"content-type": "text/event-stream"}
    )
    route = api.get("/runs/run_1/events/stream").mock(side_effect=[dropping, sse_response(sse_text(EVENTS[2:]))])
    events = list(client.runs.stream("run_1"))
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert route.calls[1].request.headers["last-event-id"] == "2"


def test_stream_gives_up_after_max_reconnects_in_a_row(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    route = api.get("/runs/run_1/events/stream").mock(return_value=sse_response(": keep-alive\n\n"))
    with pytest.raises(APIConnectionError, match="2 reconnects"):
        list(client.runs.stream("run_1", max_reconnects=2))
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]


def test_an_event_resets_the_reconnect_counter(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    answers = [
        sse_response(sse_text([EVENTS[0]])),
        sse_response(sse_text([EVENTS[1]])),
        sse_response(sse_text([EVENTS[2]])),
    ]
    answers += [sse_response(sse_text(EVENTS[3:]))]
    route = api.get("/runs/run_1/events/stream").mock(side_effect=answers)
    events = list(client.runs.stream("run_1", max_reconnects=1))
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert route.call_count == 4
    assert sleeps == [1.0, 1.0, 1.0]


def test_the_stream_retry_field_sets_the_reconnect_delay(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    first = "retry: 250\n" + sse_text(EVENTS[:2])
    api.get("/runs/run_1/events/stream").mock(side_effect=[sse_response(first), sse_response(sse_text(EVENTS[2:]))])
    events = list(client.runs.stream("run_1"))
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert sleeps == [0.25]
    api.get("/runs/run_2/events/stream").mock(
        side_effect=[sse_response("retry: 600000\n" + sse_text(EVENTS[:1])), sse_response(sse_text(EVENTS[1:]))]
    )
    list(client.runs.stream("run_2"))
    assert sleeps[-1] == 30.0


def test_unknown_event_kinds_stream_through(api: respx.MockRouter, client: Client) -> None:
    odd = {**EVENTS[2], "id": "evt_x", "seq": 3, "kind": "sparkle", "text": "A new kind of line."}
    later = {**EVENTS[4], "status": "paused_for_review", "seq": 4}
    text = sse_text([EVENTS[0], odd, later, {**EVENTS[4], "seq": 5}], keep_alive=False)
    api.get("/runs/run_1/events/stream").mock(return_value=sse_response(text))
    events = list(client.runs.stream("run_1"))
    assert [e.kind for e in events] == ["status", "sparkle", "status", "status"]
    assert events[1].text == "A new kind of line."
    assert events[2].status == "paused_for_review"
    assert events[-1].status == "succeeded"


def test_stream_status_errors_are_raised_not_retried(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    api.get("/runs/run_1/events/stream").mock(
        return_value=httpx.Response(404, json=error_body("not_found_error", "not_found", "No such run."))
    )
    with pytest.raises(NotFoundError, match="No such run"):
        list(client.runs.stream("run_1"))


def test_stream_takes_the_kind_from_the_event_name_when_the_body_lacks_it(
    api: respx.MockRouter, client: Client
) -> None:
    body = {k: v for k, v in EVENTS[4].items() if k != "kind"}
    text = f"id: 5\nevent: status\ndata: {json.dumps(body)}\n\n"
    api.get("/runs/run_1/events/stream").mock(return_value=sse_response(text))
    events = list(client.runs.stream("run_1"))
    assert len(events) == 1 and events[0].kind == "status" and events[0].status == "succeeded"


async def test_async_stream_yields_events_until_the_terminal_status(
    api: respx.MockRouter, async_client: AsyncClient
) -> None:
    api.get("/runs/run_1/events/stream").mock(return_value=sse_response(sse_text(EVENTS)))
    stream = async_client.runs.stream("run_1")
    events = [e async for e in stream]
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert stream.last_event_id == "5"


async def test_async_stream_resumes_with_last_event_id(
    api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers.get("last-event-id") is None:
            return sse_response(sse_text(EVENTS[:3]))
        return sse_response(sse_text(EVENTS[3:]))

    route = api.get("/runs/run_1/events/stream").mock(side_effect=answer)
    events = [e async for e in async_client.runs.stream("run_1")]
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert route.calls[1].request.headers["last-event-id"] == "3"
