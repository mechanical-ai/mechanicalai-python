from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Iterator
from typing import Any

import anyio
import httpx
import pytest
import respx

from mechanicalai import AsyncClient, Client

API_KEY = "mk_0000000000000000000000000000000000000000"
BASE_URL = "https://api.example.test/v1"

# ----- payloads: the contract's own examples -----------------------------------------------------------------------

PROJECT: dict[str, Any] = {
    "id": "prj_01jc4x7m2k9q",
    "object": "project",
    "name": "Motor bracket, line 3",
    "app_url": "https://app.example.test/project/9f1c2a7e",
    "created_at": "2026-11-06T10:01:50Z",
    "updated_at": "2026-11-06T10:01:50Z",
    "last_run_at": None,
    "metadata": {"ticket": "ENG-4471"},
}

FILE: dict[str, Any] = {
    "id": "file_01jc4y1n8r3t",
    "object": "file",
    "project_id": "prj_01jc4x7m2k9q",
    "name": "mounting-plate.step",
    "size": 39845120,
    "content_type": "model/step",
    "digest": "sha256:9f2c6c1e7a0b4d3f2e1a9c8b7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e",
    "status": "ready",
    "created_at": "2026-11-06T10:02:00Z",
    "expires_at": None,
}

RUN_QUEUED: dict[str, Any] = {
    "id": "run_01jc5000a1b2",
    "object": "run",
    "project_id": "prj_01jc4x7m2k9q",
    "parent_run_id": None,
    "engine": "mekint",
    "engine_version": None,
    "status": "queued",
    "cancel_requested": False,
    "problem": "Design a steel bracket that mounts the 12 kg motor ...",
    "files": ["file_01jc4y1n8r3t"],
    "units": "metric",
    "language": "en",
    "cost_cents": 0,
    "message": None,
    "summary": None,
    "next_actions": [],
    "question": None,
    "failure": None,
    "end_user": "plant-3",
    "metadata": {"ticket": "ENG-4471"},
    "webhook_url": None,
    "created_at": "2026-11-06T10:02:05Z",
    "started_at": None,
    "finished_at": None,
    "duration_s": None,
}

RUN_RUNNING: dict[str, Any] = {**RUN_QUEUED, "status": "running", "engine_version": "mekint-20261105"}

RUN_SUCCEEDED: dict[str, Any] = {
    **RUN_QUEUED,
    "engine_version": "mekint-20261105",
    "status": "succeeded",
    "cost_cents": 1420,
    "message": "A 6 mm S355 two-leg bracket, 0.94 kg, first mode 112 Hz. Peak von Mises 178 MPa at 3x load, safety "
    "factor 2.0 against yield. I assumed the motor's centre of mass 60 mm above the plate; the bracket document shows "
    "the sensitivity to that height.",
    "summary": "A 6 mm S355 two-leg bracket, 0.94 kg, first mode 112 Hz.",
    "next_actions": ["Confirm the motor centre-of-mass height."],
    "started_at": "2026-11-06T10:02:08Z",
    "finished_at": "2026-11-06T10:11:30Z",
    "duration_s": 562,
}

ARTIFACT_DOCUMENT: dict[str, Any] = {
    "id": "art_01jc5201",
    "object": "artifact",
    "project_id": "prj_01jc4x7m2k9q",
    "run_id": "run_01jc5000a1b2",
    "kind": "document",
    "name": "bracket-notes",
    "formats": ["md", "pdf"],
    "default_format": "md",
    "size": 219136,
    "group": None,
    "created_at": "2026-11-06T10:11:30Z",
}

ARTIFACT_MODEL: dict[str, Any] = {
    "id": "art_01jc5202",
    "object": "artifact",
    "project_id": "prj_01jc4x7m2k9q",
    "run_id": "run_01jc5000a1b2",
    "kind": "model",
    "name": "bracket",
    "formats": ["step", "stl", "glb", "native"],
    "default_format": "step",
    "size": 2306215,
    "group": None,
    "created_at": "2026-11-06T10:04:17Z",
}

ENGINE: dict[str, Any] = {
    "id": "mekint",
    "object": "engine",
    "snapshot": "mekint-20261105",
    "status": "current",
    "released_at": "2026-11-05T00:00:00Z",
}

USAGE: dict[str, Any] = {
    "object": "usage",
    "period": "month",
    "from": "2026-11-01T00:00:00Z",
    "to": "2026-12-01T00:00:00Z",
    "runs": 14,
    "cost_cents": 41260,
    "buckets": [{"from": "2026-11-01T00:00:00Z", "to": "2026-12-01T00:00:00Z", "runs": 14, "cost_cents": 41260}],
    "by_end_user": {"plant-3": {"runs": 11, "cost_cents": 31810}, "plant-7": {"runs": 3, "cost_cents": 9450}},
}

LIMITS: dict[str, Any] = {
    "object": "limits",
    "projects_per_user": 100,
    "files_per_project": 200,
    "storage_bytes": 53687091200,
    "concurrent_tool_runs": 4,
    "messages_per_minute": 10,
    "requests_per_minute": 120,
    "reference_file_upload_bytes": 67108864,
    "user_char_limit": 100000,
    "usage": {"projects": 12, "storage_bytes": 7340032000, "tool_runs": 1, "running_runs": 1},
}

EVENTS: list[dict[str, Any]] = [
    {
        "id": "evt_1",
        "object": "run_event",
        "run_id": "run_01jc5000a1b2",
        "seq": 1,
        "at": "2026-11-06T10:02:08Z",
        "kind": "status",
        "status": "running",
    },  # noqa: E501
    {
        "id": "evt_2",
        "object": "run_event",
        "run_id": "run_01jc5000a1b2",
        "seq": 2,
        "at": "2026-11-06T10:02:08Z",
        "kind": "phase",
        "phase": "thinking",
    },  # noqa: E501
    {
        "id": "evt_3",
        "object": "run_event",
        "run_id": "run_01jc5000a1b2",
        "seq": 3,
        "at": "2026-11-06T10:02:11Z",
        "kind": "progress",
        "text": "Reading the plate and the load cases.",
    },  # noqa: E501
    {
        "id": "evt_4",
        "object": "run_event",
        "run_id": "run_01jc5000a1b2",
        "seq": 4,
        "at": "2026-11-06T10:05:40Z",
        "kind": "task",
        "text": "Running the static study at 3x load",
        "percent": 65,
        "eta_s": 50,
    },  # noqa: E501
    {
        "id": "evt_5",
        "object": "run_event",
        "run_id": "run_01jc5000a1b2",
        "seq": 5,
        "at": "2026-11-06T10:11:30Z",
        "kind": "status",
        "status": "succeeded",
    },  # noqa: E501
]


def error_body(
    type_: str, code: str, message: str = "Refused.", param: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "error": {"type": type_, "code": code, "message": message, "param": param, "request_id": "req_body", **extra}
    }


def list_body(items: list[dict[str, Any]], *, has_more: bool = False, next_cursor: str | None = None) -> dict[str, Any]:
    return {"object": "list", "data": copy.deepcopy(items), "has_more": has_more, "next_cursor": next_cursor}


def sse_text(events: list[dict[str, Any]], *, keep_alive: bool = True) -> str:
    import json

    chunks: list[str] = []
    for i, event in enumerate(events):
        if keep_alive and i == 1:
            chunks.append(": keep-alive\n\n")
        chunks.append(f"id: {event['seq']}\nevent: {event['kind']}\ndata: {json.dumps(event)}\n\n")
    return "".join(chunks)


def sse_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, headers={"content-type": "text/event-stream"})


# ----- fixtures ----------------------------------------------------------------------------------------------------


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client() -> Iterator[Client]:
    with Client(api_key=API_KEY, base_url=BASE_URL) as c:
        yield c


@pytest.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(api_key=API_KEY, base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every sleep instead of sleeping, in the sync and the async loops."""
    recorded: list[float] = []

    def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    async def fake_async_sleep(seconds: float) -> None:
        recorded.append(seconds)

    import time

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(anyio, "sleep", fake_async_sleep)
    return recorded
