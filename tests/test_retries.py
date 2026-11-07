from __future__ import annotations

import httpx
import pytest
import respx

from mechanicalai import (
    APIConnectionError,
    APITimeoutError,
    AsyncClient,
    BadRequestError,
    Client,
    ConflictError,
    InternalServerError,
    RateLimitError,
)
from tests.conftest import API_KEY, BASE_URL, PROJECT, RUN_QUEUED, error_body


def test_429_honours_retry_after(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limit_error", "rate_limited"), headers={"Retry-After": "3"}),
            httpx.Response(200, json=PROJECT),
        ]
    )
    project = client.projects.retrieve("prj_1")
    assert project.id == PROJECT["id"]
    assert route.call_count == 2
    assert sleeps == [3.0]


def test_503_is_retried_with_backoff(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        side_effect=[
            httpx.Response(503, json=error_body("overloaded_error", "overloaded")),
            httpx.Response(503, json=error_body("overloaded_error", "server_preparing")),
            httpx.Response(200, json=PROJECT),
        ]
    )
    client.projects.retrieve("prj_1")
    assert route.call_count == 3
    assert len(sleeps) == 2
    assert 0.375 <= sleeps[0] <= 0.5
    assert 0.75 <= sleeps[1] <= 1.0


def test_retry_after_beyond_the_cap_falls_back_to_backoff(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    api.get("/projects/prj_1").mock(
        side_effect=[
            httpx.Response(503, json=error_body("overloaded_error", "overloaded"), headers={"Retry-After": "3600"}),
            httpx.Response(200, json=PROJECT),
        ]
    )
    client.projects.retrieve("prj_1")
    assert sleeps and sleeps[0] <= 0.5


def test_connection_error_is_retried(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=PROJECT)]
    )
    client.projects.retrieve("prj_1")
    assert route.call_count == 2
    assert len(sleeps) == 1


def test_connection_errors_exhaust_into_api_connection_error(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    route = api.get("/projects/prj_1").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError) as info:
        client.projects.retrieve("prj_1")
    assert route.call_count == 3
    assert info.value.request is not None
    assert info.value.request.url.path.endswith("/projects/prj_1")


def test_timeouts_exhaust_into_api_timeout_error(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    api.get("/projects/prj_1").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        client.projects.retrieve("prj_1")
    assert len(sleeps) == 2


def test_400_is_not_retried(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        return_value=httpx.Response(400, json=error_body("invalid_request_error", "invalid_parameter"))
    )
    with pytest.raises(BadRequestError):
        client.projects.retrieve("prj_1")
    assert route.call_count == 1
    assert sleeps == []


def test_409_is_not_retried(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/projects/prj_1/runs").mock(
        return_value=httpx.Response(409, json=error_body("conflict_error", "project_limit_reached"))
    )
    with pytest.raises(ConflictError):
        client.runs.create("prj_1", engine="mekint", problem="x")
    assert route.call_count == 1


def test_retries_exhaust_into_the_last_error(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        return_value=httpx.Response(500, json=error_body("api_error", "internal_error"))
    )
    with pytest.raises(InternalServerError):
        client.projects.retrieve("prj_1")
    assert route.call_count == 3


def test_max_retries_zero_means_one_attempt(api: respx.MockRouter, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limit_error", "rate_limited"), headers={"Retry-After": "1"}
        )
    )
    with Client(api_key=API_KEY, base_url=BASE_URL, max_retries=0) as client, pytest.raises(RateLimitError) as info:
        client.projects.retrieve("prj_1")
    assert route.call_count == 1
    assert info.value.retry_after == 1.0


def test_the_idempotency_key_is_kept_across_retries(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/projects").mock(
        side_effect=[
            httpx.Response(503, json=error_body("overloaded_error", "overloaded")),
            httpx.Response(201, json=PROJECT),
        ]
    )
    client.projects.create(name="x")
    keys = [call.request.headers["idempotency-key"] for call in route.calls]
    assert len(keys) == 2 and keys[0] == keys[1]


async def test_async_429_then_success(api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limit_error", "rate_limited"), headers={"Retry-After": "2"}),
            httpx.Response(200, json=PROJECT),
        ]
    )
    project = await async_client.projects.retrieve("prj_1")
    assert project.id == PROJECT["id"]
    assert route.call_count == 2
    assert sleeps == [2.0]


async def test_async_connection_error_then_success(
    api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]
) -> None:
    route = api.get("/projects/prj_1").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=PROJECT)]
    )
    await async_client.projects.retrieve("prj_1")
    assert route.call_count == 2


async def test_async_400_is_not_retried(api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]) -> None:
    route = api.get("/projects/prj_1").mock(
        return_value=httpx.Response(400, json=error_body("invalid_request_error", "invalid_parameter"))
    )
    with pytest.raises(BadRequestError):
        await async_client.projects.retrieve("prj_1")
    assert route.call_count == 1


def test_cancel_is_not_resent_after_a_connection_error(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    route = api.post("/runs/run_1/cancel").mock(side_effect=httpx.ConnectError("reset"))
    with pytest.raises(APIConnectionError):
        client.runs.cancel("run_1")
    assert route.call_count == 1
    assert sleeps == []


def test_cancel_is_not_resent_after_a_500(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/runs/run_1/cancel").mock(
        return_value=httpx.Response(500, json=error_body("api_error", "internal_error"))
    )
    with pytest.raises(InternalServerError):
        client.runs.cancel("run_1")
    assert route.call_count == 1
    assert sleeps == []


def test_cancel_is_not_resent_after_a_timeout(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/runs/run_1/cancel").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        client.runs.cancel("run_1")
    assert route.call_count == 1


def test_complete_is_not_resent_after_a_503(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/files/file_1/complete").mock(
        return_value=httpx.Response(503, json=error_body("overloaded_error", "overloaded"))
    )
    with pytest.raises(InternalServerError):
        client.files.complete("file_1")
    assert route.call_count == 1


def test_a_keyless_post_is_still_retried_on_429(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/runs/run_1/cancel").mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limit_error", "rate_limited"), headers={"Retry-After": "1"}),
            httpx.Response(200, json=RUN_QUEUED),
        ]
    )
    client.runs.cancel("run_1")
    assert route.call_count == 2
    assert sleeps == [1.0]


def test_creates_keep_their_key_and_stay_retryable(api: respx.MockRouter, client: Client, sleeps: list[float]) -> None:
    route = api.post("/runs/run_1/reply").mock(
        side_effect=[httpx.ConnectError("reset"), httpx.Response(202, json=RUN_QUEUED)]
    )
    client.runs.reply("run_1", text="Steel.")
    assert route.call_count == 2
    keys = {c.request.headers["idempotency-key"] for c in route.calls}
    assert len(keys) == 1


def test_a_burst_429_on_a_create_is_retried_after_retry_after(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    """The account's message burst answers a create or a reply with 429 ``rate_limited``; retried like any 429."""
    route = api.post("/projects/prj_1/runs").mock(
        side_effect=[
            httpx.Response(
                429,
                json=error_body("rate_limit_error", "rate_limited", "Too many requests at once. Please wait a moment."),
                headers={"Retry-After": "60"},
            ),
            httpx.Response(202, json=RUN_QUEUED),
        ]
    )
    run = client.runs.create("prj_1", engine="mekint", problem="x")
    assert run.status == "queued" and route.call_count == 2
    assert sleeps == [60.0]
    assert len({c.request.headers["idempotency-key"] for c in route.calls}) == 1


async def test_async_cancel_is_not_resent_after_a_500(
    api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]
) -> None:
    route = api.post("/runs/run_1/cancel").mock(
        return_value=httpx.Response(500, json=error_body("api_error", "internal_error"))
    )
    with pytest.raises(InternalServerError):
        await async_client.runs.cancel("run_1")
    assert route.call_count == 1
