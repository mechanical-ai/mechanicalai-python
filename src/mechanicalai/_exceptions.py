"""The exceptions the library raises.

``MechanicalAIError`` is the base of every one. Transport failures are ``APIConnectionError`` (``APITimeoutError``
when the request timed out). An error answer from the API is an ``APIStatusError`` carrying the status, the body's
``error.type`` / ``error.code`` / ``error.param`` and the ``X-Request-Id``; its subclass is chosen by status, and by
``code`` for the domain errors (``InsufficientCreditsError``, ``ProjectBusyError``, ``EngineRetiredError``).
"""

from __future__ import annotations

from typing import Any

import httpx

__all__ = [
    "APIConnectionError",
    "APIStatusError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "EngineRetiredError",
    "GoneError",
    "IdempotencyAnswerLostError",
    "IdempotencySupersededError",
    "InsufficientCreditsError",
    "InternalServerError",
    "MechanicalAIError",
    "NotFoundError",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "ProjectBusyError",
    "RateLimitError",
    "UnprocessableEntityError",
    "WaitTimeoutError",
    "WebhookSignatureError",
]


class MechanicalAIError(Exception):
    """The base of every error the library raises."""


class APIConnectionError(MechanicalAIError):
    """The request never got an answer: a connection failure, a dropped connection, a protocol error."""

    def __init__(self, message: str = "Connection error.", *, request: httpx.Request | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.request = request


class APITimeoutError(APIConnectionError):
    """The request timed out."""

    def __init__(self, *, request: httpx.Request | None = None) -> None:
        super().__init__("Request timed out.", request=request)


class APIStatusError(MechanicalAIError):
    """An error answer from the API (a 4xx or 5xx status)."""

    status_code: int
    request_id: str | None
    type: str | None
    """The error class: ``invalid_request_error``, ``authentication_error``, ``conflict_error``, ..."""
    code: str | None
    """The stable reason: ``project_busy``, ``insufficient_credit``, ... Handle by this; messages are not stable."""
    param: str | None
    """The offending field, when there is one."""
    message: str
    body: Any
    """The whole JSON body of the answer (or its text when it was not JSON)."""
    limit: int | None
    """On limit refusals: the limit."""
    count: int | None
    """On limit refusals: the count."""
    response: httpx.Response

    def __init__(self, message: str, *, response: httpx.Response, body: Any) -> None:
        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
        self.status_code = response.status_code
        self.response = response
        self.body = body
        self.type = _opt_str(error.get("type"))
        self.code = _opt_str(error.get("code"))
        self.param = _opt_str(error.get("param"))
        self.request_id = response.headers.get("x-request-id") or _opt_str(error.get("request_id"))
        self.limit = _opt_int(error.get("limit"))
        self.count = _opt_int(error.get("count"))
        self.message = message
        super().__init__(self._describe())

    def _describe(self) -> str:
        parts = [f"{self.status_code}"]
        if self.code:
            parts.append(self.code)
        text = " ".join(parts) + f": {self.message}"
        if self.request_id:
            text += f" (request_id={self.request_id})"
        return text


class BadRequestError(APIStatusError):
    """400."""


class AuthenticationError(APIStatusError):
    """401 ``invalid_api_key``: the key is missing, malformed, unknown, revoked or on the wrong host."""


class PaymentRequiredError(APIStatusError):
    """402: the API wallet's live balance is at or below zero (``insufficient_credit``), or the key's monthly cap is
    reached (``key_cap_reached``)."""


class InsufficientCreditsError(PaymentRequiredError):
    """402 ``insufficient_credit``: the API wallet's live balance is at or below zero (``param`` is null); add credit
    and send the same request again.

    A run already working when the wallet reaches zero is not refused this way: it ends ``failed`` with
    ``failure.code == "insufficient_credit"``.
    """


class PermissionDeniedError(APIStatusError):
    """403 (not sent by the API today; kept for the standard hierarchy)."""


class NotFoundError(APIStatusError):
    """404: no such object for this account."""


class ConflictError(APIStatusError):
    """409: ``project_busy``, a limit reached, ``file_in_use``, ``run_not_waiting``, ``run_finished``,
    ``idempotency_conflict`` (a different request under a used key), or the two idempotency outcomes below."""


class ProjectBusyError(ConflictError):
    """409 ``project_busy``: the project has a live run; wait for it to end or cancel it."""


class IdempotencyAnswerLostError(ConflictError):
    """409 ``idempotency_answer_lost``: the create happened but its answer was lost.

    Do not send it again, with this key or another: that would make (and charge) a second object. Read the resource
    instead, for a run by listing the project's runs.
    """


class IdempotencySupersededError(ConflictError):
    """409 ``idempotency_superseded``: a concurrent retry's answer stands.

    Do not send it again; read the resource, for a run by listing the project's runs.
    """


class GoneError(APIStatusError):
    """410 (not sent by the API today; reserved for retired pinned snapshots)."""


class EngineRetiredError(GoneError):
    """410 ``engine_retired`` (a later phase, once snapshots can be pinned): the message names the replacement."""


class UnprocessableEntityError(APIStatusError):
    """422 (not sent by the API today; kept for the standard hierarchy)."""


class RateLimitError(APIStatusError):
    """429 ``rate_limited``: the edge's rate limit, or, on a create or a reply, the account's message burst
    (``Limits.messages_per_minute``, app messages and API runs together; ``Retry-After`` is the window). Retried
    like any 429, honouring ``Retry-After`` (``retry_after``)."""

    @property
    def retry_after(self) -> float | None:
        return parse_retry_after(self.response.headers.get("retry-after"))


class InternalServerError(APIStatusError):
    """5xx: ``internal_error``, ``overloaded``, ``server_preparing`` (retried, honouring ``Retry-After``)."""


class WebhookSignatureError(MechanicalAIError):
    """A webhook delivery whose signature is missing, malformed, wrong or too old."""


class WaitTimeoutError(MechanicalAIError, TimeoutError):
    """``runs.wait`` ran out of time before the run reached a terminal status. The run itself keeps going."""


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` as seconds: an integer, or an HTTP date relative to now."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        from datetime import datetime, timezone

        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    return max(0.0, seconds)


def status_error_from_response(response: httpx.Response, body: Any) -> APIStatusError:
    """The exception for an error answer: by status, then by ``code`` for the domain errors."""
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    message = _opt_str(error.get("message")) or (response.reason_phrase or f"HTTP {response.status_code}")
    code = _opt_str(error.get("code"))
    status = response.status_code
    if code in ("idempotency_answer_lost", "idempotency_superseded"):
        message += " The create happened; do not send it again. Read the resource (for a run, list the project's runs)."

    cls: type[APIStatusError]
    if status == 400:
        cls = BadRequestError
    elif status == 401:
        cls = AuthenticationError
    elif status == 402:
        cls = InsufficientCreditsError if code == "insufficient_credit" else PaymentRequiredError
    elif status == 403:
        cls = PermissionDeniedError
    elif status == 404:
        cls = NotFoundError
    elif status == 409:
        cls = {
            "project_busy": ProjectBusyError,
            "idempotency_answer_lost": IdempotencyAnswerLostError,
            "idempotency_superseded": IdempotencySupersededError,
        }.get(code or "", ConflictError)
    elif status == 410:
        cls = EngineRetiredError if code == "engine_retired" else GoneError
    elif status == 422:
        cls = UnprocessableEntityError
    elif status == 429:
        cls = RateLimitError
    elif status >= 500:
        cls = InternalServerError
    else:
        cls = APIStatusError
    return cls(message, response=response, body=body)
