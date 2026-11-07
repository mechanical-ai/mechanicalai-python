from __future__ import annotations

import httpx
import pytest
import respx

from mechanicalai import (
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    Client,
    ConflictError,
    EngineRetiredError,
    GoneError,
    IdempotencyAnswerLostError,
    IdempotencySupersededError,
    InsufficientCreditsError,
    InternalServerError,
    MechanicalAIError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    ProjectBusyError,
    RateLimitError,
    UnprocessableEntityError,
)
from tests.conftest import error_body


@pytest.mark.parametrize(
    ("status", "type_", "code", "expected"),
    [
        (400, "invalid_request_error", "invalid_parameter", BadRequestError),
        (401, "authentication_error", "invalid_api_key", AuthenticationError),
        (402, "payment_required_error", "key_cap_reached", PaymentRequiredError),
        (402, "payment_required_error", "insufficient_credit", InsufficientCreditsError),
        (403, "permission_error", "forbidden", PermissionDeniedError),
        (404, "not_found_error", "not_found", NotFoundError),
        (409, "conflict_error", "project_limit_reached", ConflictError),
        (409, "conflict_error", "project_busy", ProjectBusyError),
        (409, "conflict_error", "idempotency_conflict", ConflictError),
        (409, "conflict_error", "idempotency_answer_lost", IdempotencyAnswerLostError),
        (409, "conflict_error", "idempotency_superseded", IdempotencySupersededError),
        (409, "conflict_error", "file_in_use", ConflictError),
        (410, "gone_error", "engine_retired", EngineRetiredError),
        (410, "gone_error", "something_else", GoneError),
        (413, "invalid_request_error", "file_too_large", APIStatusError),
        (422, "invalid_request_error", "unprocessable", UnprocessableEntityError),
        (429, "rate_limit_error", "rate_limited", RateLimitError),
        (500, "api_error", "internal_error", InternalServerError),
        (503, "overloaded_error", "server_preparing", InternalServerError),
    ],
)
def test_status_and_code_choose_the_exception(
    api: respx.MockRouter, client: Client, status: int, type_: str, code: str, expected: type[APIStatusError]
) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(status, json=error_body(type_, code, param="engine")))
    with pytest.raises(expected) as info:
        client.with_options(max_retries=0).runs.retrieve("run_1")
    exc = info.value
    assert type(exc) is expected
    assert exc.status_code == status
    assert exc.type == type_
    assert exc.code == code
    assert exc.param == "engine"
    assert isinstance(exc, APIStatusError)
    assert isinstance(exc, MechanicalAIError)


def test_the_hierarchy() -> None:
    assert issubclass(InsufficientCreditsError, PaymentRequiredError)
    assert issubclass(ProjectBusyError, ConflictError)
    assert issubclass(IdempotencyAnswerLostError, ConflictError)
    assert issubclass(IdempotencySupersededError, ConflictError)
    assert issubclass(EngineRetiredError, GoneError)
    assert issubclass(RateLimitError, APIStatusError)
    assert issubclass(APIStatusError, MechanicalAIError)


def test_error_attributes_and_message(api: respx.MockRouter, client: Client) -> None:
    body = error_body(
        "conflict_error",
        "storage_quota_reached",
        "Storage quota reached (50 GB).",
        limit=53687091200,
        count=53700000000,
    )
    api.post("/projects/prj_1/files/upload-url").mock(
        return_value=httpx.Response(409, json=body, headers={"X-Request-Id": "req_header"})
    )
    with pytest.raises(ConflictError) as info:
        client.files.create_upload_url("prj_1", name="a.step", size=1, content_type="model/step")
    exc = info.value
    assert exc.message == "Storage quota reached (50 GB)."
    assert exc.request_id == "req_header"
    assert exc.limit == 53687091200
    assert exc.count == 53700000000
    assert exc.body == body
    assert exc.response.status_code == 409
    assert str(exc) == "409 storage_quota_reached: Storage quota reached (50 GB). (request_id=req_header)"


def test_request_id_falls_back_to_the_body(api: respx.MockRouter, client: Client) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(404, json=error_body("not_found_error", "not_found")))
    with pytest.raises(NotFoundError) as info:
        client.runs.retrieve("run_1")
    assert info.value.request_id == "req_body"


def test_non_json_error_bodies(api: respx.MockRouter, client: Client) -> None:
    api.get("/runs/run_1").mock(
        return_value=httpx.Response(502, text="bad gateway", headers={"content-type": "text/html"})
    )
    with pytest.raises(InternalServerError) as info:
        client.with_options(max_retries=0).runs.retrieve("run_1")
    exc = info.value
    assert exc.body == "bad gateway"
    assert exc.code is None
    assert exc.message == "Bad Gateway"
    assert str(exc) == "502: Bad Gateway"


def test_rate_limit_retry_after(api: respx.MockRouter, client: Client) -> None:
    api.get("/runs/run_1").mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limit_error", "rate_limited"), headers={"Retry-After": "7"}
        )
    )
    with pytest.raises(RateLimitError) as info:
        client.with_options(max_retries=0).runs.retrieve("run_1")
    assert info.value.retry_after == 7.0


def test_insufficient_credit_reads_like_the_contract(api: respx.MockRouter, client: Client) -> None:
    api.post("/projects/prj_1/runs").mock(
        return_value=httpx.Response(
            402,
            json=error_body(
                "payment_required_error",
                "insufficient_credit",
                "Your API wallet balance is -$0.12. Add credit to the wallet to continue.",
            ),
        )
    )
    with pytest.raises(InsufficientCreditsError) as info:
        client.runs.create("prj_1", engine="mekint", problem="x")
    assert info.value.code == "insufficient_credit" and info.value.param is None
    assert "wallet" in info.value.message
