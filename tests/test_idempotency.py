from __future__ import annotations

import re

import httpx
import pytest
import respx

from mechanicalai import (
    AsyncClient,
    Client,
    ConflictError,
    IdempotencyAnswerLostError,
    IdempotencySupersededError,
)
from tests.conftest import API_KEY, BASE_URL, FILE, PROJECT, RUN_QUEUED, error_body, list_body

KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _is_generated(value: str) -> bool:
    return bool(KEY_PATTERN.match(value)) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def test_creates_get_a_generated_key(api: respx.MockRouter, client: Client) -> None:
    projects = api.post("/projects").mock(return_value=httpx.Response(201, json=PROJECT))
    runs = api.post("/projects/prj_1/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    new_project_runs = api.post("/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    replies = api.post("/runs/run_1/reply").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    uploads = api.post("/projects/prj_1/files").mock(return_value=httpx.Response(201, json=FILE))
    upload_urls = api.post("/projects/prj_1/files/upload-url").mock(
        return_value=httpx.Response(
            201,
            json={
                "file": FILE,
                "upload_url": "https://u.example.test/x",
                "upload_headers": {},
                "expires_at": "2026-11-06T10:12:00Z",
            },
        )
    )

    client.projects.create()
    client.runs.create("prj_1", engine="mekint", problem="x", budget_cents=100)
    client.runs.create_in_new_project(engine="mekint", problem="x", budget_cents=100)
    client.runs.reply("run_1", text="Steel, 10 mm.")
    client.files.upload("prj_1", b"solid", name="part.stl")
    client.files.create_upload_url("prj_1", name="big.step", size=10, content_type="model/step")

    routes = (projects, runs, new_project_runs, replies, uploads, upload_urls)
    keys = [r.calls.last.request.headers["idempotency-key"] for r in routes]
    assert all(_is_generated(k) for k in keys)
    assert len(set(keys)) == len(keys)


def test_caller_provided_key_is_sent_as_is(api: respx.MockRouter, client: Client) -> None:
    route = api.post("/projects/prj_1/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    client.runs.create("prj_1", engine="mekint", problem="x", budget_cents=100, idempotency_key="order-4471:run.1")
    assert route.calls.last.request.headers["idempotency-key"] == "order-4471:run.1"


@pytest.mark.parametrize("bad", ["", "a" * 65, "has space", "slash/", "ünïcode"])
def test_a_key_outside_the_grammar_is_refused_before_sending(api: respx.MockRouter, client: Client, bad: str) -> None:
    route = api.post("/projects").mock(return_value=httpx.Response(201, json=PROJECT))
    with pytest.raises(ValueError, match="Idempotency-Key"):
        client.projects.create(idempotency_key=bad)
    assert route.call_count == 0


def test_non_creating_requests_carry_no_key(api: respx.MockRouter, client: Client) -> None:
    cancel = api.post("/runs/run_1/cancel").mock(return_value=httpx.Response(200, json=RUN_QUEUED))
    complete = api.post("/files/file_1/complete").mock(return_value=httpx.Response(200, json=FILE))
    get = api.get("/projects").mock(return_value=httpx.Response(200, json=list_body([])))
    patch = api.patch("/projects/prj_1").mock(return_value=httpx.Response(200, json=PROJECT))
    delete = api.delete("/projects/prj_1").mock(return_value=httpx.Response(204))

    client.runs.cancel("run_1")
    client.files.complete("file_1")
    client.projects.list()
    client.projects.update("prj_1", name="renamed")
    client.projects.delete("prj_1")
    for route in (cancel, complete, get, patch, delete):
        assert "idempotency-key" not in route.calls.last.request.headers


def test_in_progress_is_retried_with_the_same_key_after_retry_after(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    route = api.post("/projects").mock(
        side_effect=[
            httpx.Response(
                409, json=error_body("conflict_error", "idempotency_in_progress"), headers={"Retry-After": "2"}
            ),
            httpx.Response(201, json=PROJECT, headers={"Idempotent-Replayed": "true"}),
        ]
    )
    project = client.projects.create(name="x", idempotency_key="mine")
    assert project.id == PROJECT["id"]
    assert [c.request.headers["idempotency-key"] for c in route.calls] == ["mine", "mine"]
    assert sleeps == [2.0]


@pytest.mark.parametrize(
    ("code", "expected"),
    [("idempotency_answer_lost", IdempotencyAnswerLostError), ("idempotency_superseded", IdempotencySupersededError)],
)
@pytest.mark.parametrize("given", [None, "mine"])
def test_lost_or_superseded_is_raised_never_resent(
    api: respx.MockRouter,
    client: Client,
    sleeps: list[float],
    code: str,
    expected: type[ConflictError],
    given: str | None,
) -> None:
    route = api.post("/projects/prj_1/runs").mock(
        return_value=httpx.Response(409, json=error_body("conflict_error", code, "The answer was lost."))
    )
    with pytest.raises(expected) as info:
        client.runs.create("prj_1", engine="mekint", problem="x", budget_cents=100, idempotency_key=given)
    assert info.value.code == code
    assert isinstance(info.value, ConflictError)
    assert "list the project's runs" in str(info.value)
    assert route.call_count == 1
    assert sleeps == []


def test_a_conflict_is_never_retried(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/projects").mock(
        return_value=httpx.Response(409, json=error_body("conflict_error", "idempotency_conflict"))
    )
    with pytest.raises(ConflictError):
        client.projects.create(name="x")
    assert route.call_count == 1


def test_other_409s_are_not_retried_even_with_a_key(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/projects/prj_1/runs").mock(
        return_value=httpx.Response(409, json=error_body("conflict_error", "project_busy"))
    )
    with pytest.raises(ConflictError):
        client.runs.create("prj_1", engine="mekint", problem="x", budget_cents=100)
    assert route.call_count == 1


async def test_async_creates_get_a_key_and_retry_in_progress(
    api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]
) -> None:
    route = api.post("/projects").mock(
        side_effect=[
            httpx.Response(
                409, json=error_body("conflict_error", "idempotency_in_progress"), headers={"Retry-After": "2"}
            ),
            httpx.Response(201, json=PROJECT),
            httpx.Response(201, json=PROJECT),
        ]
    )
    await async_client.projects.create(name="x")
    keys = [c.request.headers["idempotency-key"] for c in route.calls]
    assert keys[0] == keys[1] and _is_generated(keys[0])
    assert sleeps == [2.0]
    await async_client.projects.create(name="x", idempotency_key="given")
    assert route.calls.last.request.headers["idempotency-key"] == "given"


def _in_progress() -> httpx.Response:
    return httpx.Response(
        409, json=error_body("conflict_error", "idempotency_in_progress"), headers={"Retry-After": "2"}
    )


def test_in_progress_is_waited_out_for_thirty_seconds_then_raised(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    route = api.post("/projects").mock(return_value=_in_progress())
    with pytest.raises(ConflictError) as info:
        client.projects.create(name="x", idempotency_key="mine")
    assert info.value.code == "idempotency_in_progress"
    assert sleeps == [2.0] * 15
    assert route.call_count == 16
    assert len({c.request.headers["idempotency-key"] for c in route.calls}) == 1


def test_in_progress_resends_outside_the_retry_count(api: respx.MockRouter, sleeps: list[float]) -> None:
    route = api.post("/projects").mock(side_effect=[_in_progress(), _in_progress(), httpx.Response(201, json=PROJECT)])
    with Client(api_key=API_KEY, base_url=BASE_URL, max_retries=0) as client:
        project = client.projects.create(name="x")
    assert project.id == PROJECT["id"]
    assert route.call_count == 3
    assert sleeps == [2.0, 2.0]


def test_in_progress_without_retry_after_waits_two_seconds(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    api.post("/projects").mock(
        side_effect=[
            httpx.Response(409, json=error_body("conflict_error", "idempotency_in_progress")),
            httpx.Response(201, json=PROJECT),
        ]
    )
    client.projects.create(name="x")
    assert sleeps == [2.0]
