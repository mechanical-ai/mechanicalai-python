from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from mechanicalai import AsyncClient, Client, WaitTimeoutError
from mechanicalai.types import TERMINAL_RUN_STATUSES, Run
from tests.conftest import ENGINE, EVENTS, LIMITS, PROJECT, RUN_QUEUED, RUN_RUNNING, RUN_SUCCEEDED, USAGE, list_body


def test_create_sends_only_what_was_given(api: respx.MockRouter, client: Client) -> None:
    route = api.post("/projects/prj_1/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    run = client.runs.create(
        "prj_1",
        engine="mekint",
        problem="Design a bracket.",
        files=("file_1",),
        units="metric",
        end_user="plant-3",
        metadata={"ticket": "ENG-4471"},
    )
    assert json.loads(route.calls.last.request.content) == {
        "engine": "mekint",
        "problem": "Design a bracket.",
        "files": ["file_1"],
        "units": "metric",
        "end_user": "plant-3",
        "metadata": {"ticket": "ENG-4471"},
    }
    assert run.status == "queued" and not run.is_terminal
    assert run.created_at == datetime(2026, 11, 6, 10, 2, 5, tzinfo=timezone.utc)
    assert run.metadata == {"ticket": "ENG-4471"}


def test_create_in_new_project(api: respx.MockRouter, client: Client) -> None:
    route = api.post("/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    client.runs.create_in_new_project(engine="mekint", problem="x", language="ko")
    assert json.loads(route.calls.last.request.content) == {
        "engine": "mekint",
        "problem": "x",
        "language": "ko",
    }
    client.runs.create_in_new_project(engine="mekint", problem="x", project_id="prj_9")
    assert json.loads(route.calls.last.request.content)["project_id"] == "prj_9"


def test_retrieve_list_cancel_reply(api: respx.MockRouter, client: Client) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(200, json=RUN_SUCCEEDED))
    api.get("/projects/prj_1/runs").mock(return_value=httpx.Response(200, json=list_body([RUN_SUCCEEDED, RUN_QUEUED])))
    cancel = api.post("/runs/run_1/cancel").mock(
        return_value=httpx.Response(200, json={**RUN_RUNNING, "cancel_requested": True})
    )
    reply = api.post("/runs/run_1/reply").mock(
        return_value=httpx.Response(202, json={**RUN_QUEUED, "id": "run_2", "parent_run_id": "run_1"})
    )

    run = client.runs.retrieve("run_1")
    assert run.is_terminal and run.cost_cents == 1420 and run.summary and run.next_actions
    assert run.message is not None and run.message.startswith(run.summary)
    assert "budget_cents" not in run.to_dict() and "report_artifact_id" not in run.to_dict()
    assert run.duration_s == 562 and run.engine_version == "mekint-20261105"
    assert [r.status for r in client.runs.list("prj_1")] == ["succeeded", "queued"]

    cancelled = client.runs.cancel("run_1")
    assert cancelled.cancel_requested is True
    assert cancel.calls.last.request.content == b""

    answer = client.runs.reply("run_1", text="Steel, 10 mm.")
    assert answer.parent_run_id == "run_1"
    assert json.loads(reply.calls.last.request.content) == {"text": "Steel, 10 mm."}


def test_wait_polls_until_terminal(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.get("/runs/run_1").mock(
        side_effect=[
            httpx.Response(200, json=RUN_QUEUED),
            httpx.Response(200, json=RUN_RUNNING),
            httpx.Response(200, json=RUN_SUCCEEDED),
        ]
    )
    run = client.runs.wait("run_1", poll_interval=0.5)
    assert run.status == "succeeded"
    assert route.call_count == 3
    assert sleeps == [0.5, 0.5]


def test_wait_times_out(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(200, json=RUN_RUNNING))
    with pytest.raises(WaitTimeoutError, match="still running"):
        client.runs.wait("run_1", poll_interval=0.1, timeout=0)


def test_needs_input_is_terminal_for_wait(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    api.get("/runs/run_1").mock(
        return_value=httpx.Response(
            200, json={**RUN_RUNNING, "status": "needs_input", "question": "Steel or aluminium?"}
        )
    )
    run = client.runs.wait("run_1")
    assert run.status == "needs_input" and run.question == "Steel or aluminium?"
    assert TERMINAL_RUN_STATUSES == {"needs_input", "succeeded", "failed", "cancelled"}


def test_failure_is_parsed(api: respx.MockRouter, client: Client) -> None:
    api.get("/runs/run_1").mock(
        return_value=httpx.Response(
            200,
            json={
                **RUN_RUNNING,
                "status": "failed",
                "failure": {"code": "engine_error", "message": "The engine stopped."},
            },
        )
    )
    run = client.runs.retrieve("run_1")
    assert run.failure is not None and run.failure.code == "engine_error"
    api.get("/runs/run_2").mock(
        return_value=httpx.Response(
            200, json={**RUN_RUNNING, "status": "failed", "failure": {"code": "engine_gave_up", "message": "No."}}
        )
    )
    assert client.runs.retrieve("run_2").failure.code == "engine_gave_up"  # type: ignore[union-attr]
    api.get("/runs/run_3").mock(
        return_value=httpx.Response(
            200,
            json={
                **RUN_RUNNING,
                "status": "failed",
                "cost_cents": 1240,
                "failure": {"code": "insufficient_credit", "message": "The account ran out of credit."},
            },
        )
    )
    stopped = client.runs.retrieve("run_3")
    assert stopped.is_terminal and stopped.status == "failed" and stopped.message is None
    assert stopped.failure is not None and stopped.failure.code == "insufficient_credit"


def test_unknown_fields_are_kept(api: respx.MockRouter, client: Client) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(200, json={**RUN_SUCCEEDED, "new_field": {"x": 1}}))
    run = client.runs.retrieve("run_1")
    assert run.model_extra == {"new_field": {"x": 1}}
    assert run.to_dict()["new_field"] == {"x": 1}
    assert isinstance(run, Run)


def test_events_list(api: respx.MockRouter, client: Client) -> None:
    route = api.get("/runs/run_1/events").mock(return_value=httpx.Response(200, json=list_body(EVENTS)))
    events = client.runs.events.list("run_1", after="2", limit=50).data
    assert dict(route.calls.last.request.url.params) == {"after": "2", "limit": "50"}
    assert events[0].status == "running" and events[1].phase == "thinking"


def test_projects_round_trip(api: respx.MockRouter, client: Client) -> None:
    api.get("/projects/prj_1").mock(return_value=httpx.Response(200, json=PROJECT))
    update = api.patch("/projects/prj_1").mock(return_value=httpx.Response(200, json={**PROJECT, "name": None}))
    project = client.projects.retrieve("prj_1")
    assert project.name == "Motor bracket, line 3" and project.last_run_at is None
    renamed = client.projects.update("prj_1", name=None)
    assert renamed.name is None
    assert json.loads(update.calls.last.request.content) == {"name": None}
    client.projects.update("prj_1", metadata={"ticket": "ENG-1"})
    assert json.loads(update.calls.last.request.content) == {"metadata": {"ticket": "ENG-1"}}


def test_engines_usage_limits(api: respx.MockRouter, client: Client) -> None:
    api.get("/engines").mock(return_value=httpx.Response(200, json=list_body([ENGINE])))
    usage = api.get("/usage").mock(return_value=httpx.Response(200, json=USAGE))
    api.get("/limits").mock(return_value=httpx.Response(200, json=LIMITS))
    engines = client.engines.list()
    assert engines.data[0].id == "mekint" and engines.data[0].snapshot == "mekint-20261105"
    assert engines.data[0].status == "current"
    window = client.usage.retrieve(
        period="month", from_=datetime(2026, 11, 1, tzinfo=timezone.utc), group_by="end_user"
    )
    assert dict(usage.calls.last.request.url.params) == {
        "period": "month",
        "from": "2026-11-01T00:00:00Z",
        "group_by": "end_user",
    }
    assert window.runs == 14 and window.by_end_user["plant-3"].cost_cents == 31810
    assert window.buckets[0].runs == 14 and window.buckets[0].from_.month == 11
    assert window.from_.year == 2026 and window.to_dict()["from"] == "2026-11-01T00:00:00Z"
    limits = client.usage.limits()
    assert limits.reference_file_upload_bytes == 67108864 and limits.files_per_project == 200
    assert limits.projects_per_user == 100 and limits.concurrent_tool_runs == 4 and limits.user_char_limit == 100000
    assert limits.messages_per_minute == 10
    assert limits.usage.running_runs == 1 and limits.usage.tool_runs == 1 and limits.usage.projects == 12


async def test_async_create_and_wait(api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]) -> None:
    api.post("/projects/prj_1/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    api.get("/runs/run_01jc5000a1b2").mock(
        side_effect=[httpx.Response(200, json=RUN_RUNNING), httpx.Response(200, json=RUN_SUCCEEDED)]
    )
    run = await async_client.runs.create("prj_1", engine="mekint", problem="x")
    done = await async_client.runs.wait(run.id, poll_interval=1)
    assert done.status == "succeeded"
    assert sleeps == [1]


async def test_async_wait_times_out(api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(200, json=RUN_RUNNING))
    with pytest.raises(WaitTimeoutError):
        await async_client.runs.wait("run_1", timeout=0)


def test_an_unknown_status_is_not_terminal(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    api.get("/runs/run_1").mock(
        side_effect=[
            httpx.Response(200, json={**RUN_RUNNING, "status": "reviewing"}),
            httpx.Response(200, json=RUN_SUCCEEDED),
        ]
    )
    assert client.runs.retrieve("run_1").status == "reviewing"
    assert client.runs.wait("run_1", poll_interval=0.1).status == "succeeded"
