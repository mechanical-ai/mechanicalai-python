"""The httpx transport under the sync and async clients.

Every request carries the bearer key, the User-Agent and the default headers; a ``POST`` that creates carries an
``Idempotency-Key`` (generated when the caller gives none, and kept across retries). Connection errors, timeouts,
429 and 5xx answers are retried with exponential backoff and jitter, honouring ``Retry-After`` up to 60 s, except
that a ``POST`` without an ``Idempotency-Key`` (``cancel``, ``complete``) is never sent again after a failure that
may have reached the server: only its 429 is retried. A 409 ``idempotency_in_progress`` is sent again as is after
its ``Retry-After`` for up to 30 s, outside the retry count. Nothing else is retried: a 409
``idempotency_answer_lost`` or ``idempotency_superseded`` means the write happened, so it is raised for the caller
to read the resource, never resent under a new key (that would make and charge a second run).

Signed upload and download URLs on other hosts go through a second client without the key, shared between a client
and its ``with_options`` copies, with the same retry policy.

Set ``MECHANICALAI_LOG=info`` to log every request and answer, ``debug`` to log bodies too (the key and signed URL
query strings are redacted).
"""

from __future__ import annotations

import contextlib
import json as _json
import logging
import os
import platform
import random
import re
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Iterator, Mapping
from typing import Any, NoReturn

import anyio
import httpx

from mechanicalai._exceptions import (
    APIConnectionError,
    APITimeoutError,
    parse_retry_after,
    status_error_from_response,
)
from mechanicalai._version import __version__

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "AsyncAPIClient",
    "BaseClient",
    "SyncAPIClient",
]

log = logging.getLogger("mechanicalai")

DEFAULT_BASE_URL = "https://api.mechanicalai.com/v1"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
DEFAULT_MAX_RETRIES = 2

INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
MAX_RETRY_AFTER = 60.0
"""A ``Retry-After`` longer than this is ignored and the backoff used instead."""

IN_PROGRESS_BUDGET_S = 30.0
"""How long a 409 ``idempotency_in_progress`` is waited out (resending the same request) before it is raised."""
IN_PROGRESS_DEFAULT_DELAY_S = 2.0

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})
_SIGNED_QUERY = re.compile(
    r"([?&](?:X-Goog-[A-Za-z-]+|X-Amz-[A-Za-z-]+|GoogleAccessId|[Ss]ignature|sig|token|[Ee]xpires)=)[^&\s\"'\\]*"
)


def _configure_logging() -> None:
    level_name = os.environ.get("MECHANICALAI_LOG", "").strip().lower()
    if not level_name:
        return
    level = {"debug": logging.DEBUG, "info": logging.INFO}.get(level_name)
    if level is None:
        return
    log.setLevel(level)
    if not log.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        log.addHandler(handler)


def _user_agent() -> str:
    return f"mechanicalai-python/{__version__} python/{platform.python_version()} httpx/{httpx.__version__}"


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: ("[redacted]" if k.lower() in _SENSITIVE_HEADERS else v) for k, v in headers.items()}


def redact_text(text: str) -> str:
    """Blank the query parameters of signed URLs (``X-Goog-Signature=...`` and friends) in a logged body."""
    return _SIGNED_QUERY.sub(r"\1[redacted]", text)


def _is_json(response: httpx.Response) -> bool:
    content_type = str(response.headers.get("content-type", ""))
    return content_type.split(";")[0].strip().lower() == "application/json"


class _ExternalClients:
    """The clients for signed URLs on other hosts: no API key, made on first use under a lock, shared with copies."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sync: httpx.Client | None = None
        self._async: httpx.AsyncClient | None = None

    def sync(self) -> httpx.Client:
        with self._lock:
            if self._sync is None:
                self._sync = httpx.Client(headers={"User-Agent": _user_agent()})
            return self._sync

    def async_(self) -> httpx.AsyncClient:
        with self._lock:
            if self._async is None:
                self._async = httpx.AsyncClient(headers={"User-Agent": _user_agent()})
            return self._async

    def close(self) -> None:
        with self._lock:
            client, self._sync = self._sync, None
        if client is not None:
            client.close()

    async def aclose(self) -> None:
        with self._lock:
            client, self._async = self._async, None
        if client is not None:
            await client.aclose()


class BaseClient:
    """What the sync and async clients share: options, headers, URLs, the retry policy, error mapping."""

    api_key: str
    base_url: str
    timeout: float | httpx.Timeout
    max_retries: int
    default_headers: dict[str, str]

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
        default_headers: Mapping[str, str] | None = None,
        external_clients: _ExternalClients | None = None,
    ) -> None:
        _configure_logging()
        key = api_key if api_key is not None else os.environ.get("MECHANICALAI_API_KEY")
        if not key:
            raise ValueError(
                "No API key. Pass api_key= or set the MECHANICALAI_API_KEY environment variable "
                "(a key is mk_ followed by 40 characters)."
            )
        self.api_key = key
        url = base_url if base_url is not None else os.environ.get("MECHANICALAI_BASE_URL") or DEFAULT_BASE_URL
        self.base_url = url.rstrip("/")
        self.timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self.max_retries = DEFAULT_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
        self.default_headers = dict(default_headers or {})
        self._externals = external_clients if external_clients is not None else _ExternalClients()

    # ----- options -------------------------------------------------------------------------------------------------

    def _options(self) -> dict[str, Any]:
        return {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "default_headers": dict(self.default_headers),
        }

    # ----- requests ------------------------------------------------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, extra: Mapping[str, str] | None, *, idempotency_key: str | None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": _user_agent(),
            "Accept": "application/json",
        }
        headers.update(self.default_headers)
        if extra:
            headers.update({k: v for k, v in extra.items() if v is not None})
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _new_idempotency_key() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _check_idempotency_key(key: str) -> str:
        if not IDEMPOTENCY_KEY_PATTERN.match(key):
            raise ValueError("An Idempotency-Key is 1 to 64 characters of [A-Za-z0-9._:-].")
        return key

    def _resolve_key(self, *, idempotent: bool, idempotency_key: str | None) -> str | None:
        if idempotency_key is not None:
            return self._check_idempotency_key(idempotency_key)
        return self._new_idempotency_key() if idempotent else None

    @staticmethod
    def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not params:
            return None
        cleaned = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items() if v is not None}
        return cleaned or None

    @staticmethod
    def _clean_json(body: Mapping[str, Any] | None, *, strip_none: bool) -> dict[str, Any] | None:
        """The JSON body to send: None values mean "not given" and are left out, unless ``strip_none`` is off."""
        if body is None:
            return None
        return {k: v for k, v in body.items() if v is not None or not strip_none}

    # ----- the retry policy ----------------------------------------------------------------------------------------

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        if not _is_json(response):
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return code if isinstance(code, str) else None

    @staticmethod
    def _may_resend(method: str, key: str | None, status: int | None) -> bool:
        """Whether a request may go again after a failure that may have reached the server.

        A ``POST`` without an ``Idempotency-Key`` is never resent on a connection error, a timeout or a 5xx (the
        server may have acted on it); its 429 was refused before any work, so that one is.
        """
        if method.upper() != "POST" or key is not None:
            return True
        return status == 429

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """Seconds to wait before retry number ``attempt`` (0-based): ``Retry-After`` when sane, else backoff."""
        if response is not None:
            after = parse_retry_after(response.headers.get("retry-after"))
            if after is not None and 0 <= after <= MAX_RETRY_AFTER:
                return after
        base = min(INITIAL_RETRY_DELAY * (2.0**attempt), MAX_RETRY_DELAY)
        return base * (1 - random.random() * 0.25)

    def _retry_delay_for(self, response: httpx.Response, attempt: int, *, method: str, key: str | None) -> float | None:
        """Seconds before the same request is sent again, or None when this answer is final."""
        status = response.status_code
        if attempt >= self.max_retries or not (status == 429 or status >= 500):
            return None
        if not self._may_resend(method, key, status):
            return None
        return self._retry_delay(attempt, response)

    def _in_progress_delay(self, response: httpx.Response, *, key: str | None, waited: float) -> float | None:
        """For a 409 ``idempotency_in_progress``: seconds before resending, or None when it is not one or the
        budget is spent."""
        if response.status_code != 409 or key is None or self._error_code(response) != "idempotency_in_progress":
            return None
        after = parse_retry_after(response.headers.get("retry-after"))
        delay = after if after is not None and 0 < after <= MAX_RETRY_AFTER else IN_PROGRESS_DEFAULT_DELAY_S
        if waited + delay > IN_PROGRESS_BUDGET_S:
            return None
        return delay

    @staticmethod
    def _body_of(response: httpx.Response) -> Any:
        if not response.content:
            return None
        if _is_json(response):
            try:
                return response.json()
            except ValueError:
                return response.text
        return response.text

    def _raise_error(self, response: httpx.Response) -> NoReturn:
        body = self._body_of(response)
        error = status_error_from_response(response, body)
        log.info(
            "%s %s -> %s %s",
            response.request.method,
            redact_text(str(response.request.url)),
            response.status_code,
            error.code,
        )
        raise error

    # ----- logging -------------------------------------------------------------------------------------------------

    def _log_request(self, request: httpx.Request, body: Any) -> None:
        if not log.isEnabledFor(logging.INFO):
            return
        log.info("%s %s", request.method, redact_text(str(request.url)))
        if log.isEnabledFor(logging.DEBUG):
            log.debug("headers %s", _redact_headers(dict(request.headers)))
            if body is not None:
                log.debug("body %s", redact_text(_json.dumps(body, default=str)[:4000]))

    def _log_response(self, response: httpx.Response, *, streamed: bool = False) -> None:
        if not log.isEnabledFor(logging.INFO):
            return
        log.info(
            "%s %s -> %s request_id=%s",
            response.request.method,
            redact_text(str(response.request.url)),
            response.status_code,
            response.headers.get("x-request-id"),
        )
        if log.isEnabledFor(logging.DEBUG) and not streamed:
            log.debug("response headers %s", _redact_headers(dict(response.headers)))
            log.debug("response body %s", redact_text(response.text[:4000]))


class SyncAPIClient(BaseClient):
    _http: httpx.Client

    def __init__(self, *, http_client: httpx.Client | None = None, **options: Any) -> None:
        super().__init__(**options)
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(timeout=self.timeout)

    # ----- lifecycle -----------------------------------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
            self._externals.close()

    def __enter__(self: Any) -> Any:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def _external_client(self) -> httpx.Client:
        return self._externals.sync()

    # ----- the request loop ----------------------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool = False,
        idempotency_key: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        stream: bool = False,
        strip_none: bool = True,
    ) -> httpx.Response:
        """One API call with the retry policy applied. Raises an ``APIStatusError`` on an error answer."""
        key = self._resolve_key(idempotent=idempotent, idempotency_key=idempotency_key)
        body = self._clean_json(json, strip_none=strip_none)
        attempt = 0
        waited_in_progress = 0.0
        while True:
            request = self._http.build_request(
                method,
                self._url(path),
                params=self._clean_params(params),
                json=body,
                data=data,
                files=files,
                headers=self._headers(headers, idempotency_key=key),
                timeout=self.timeout if timeout is None else timeout,
            )
            self._log_request(request, body)
            try:
                response = self._http.send(request, stream=stream)
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries or not self._may_resend(method, key, None):
                    raise APITimeoutError(request=request) from exc
                delay = self._retry_delay(attempt, None)
                log.info("timeout; retry %d/%d in %.2fs", attempt + 1, self.max_retries, delay)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries or not self._may_resend(method, key, None):
                    raise APIConnectionError(str(exc) or "Connection error.", request=request) from exc
                delay = self._retry_delay(attempt, None)
                log.info("connection error (%s); retry %d/%d in %.2fs", exc, attempt + 1, self.max_retries, delay)
            else:
                self._log_response(response, streamed=stream)
                if response.is_success:
                    return response
                if stream:
                    response.read()
                pause = self._in_progress_delay(response, key=key, waited=waited_in_progress)
                if pause is not None:
                    waited_in_progress += pause
                    log.info("idempotency_in_progress; resending in %.1fs", pause)
                    response.close()
                    time.sleep(pause)
                    continue
                wait = self._retry_delay_for(response, attempt, method=method, key=key)
                if wait is None:
                    self._raise_error(response)
                delay = wait
                log.info("%s; retry %d/%d in %.2fs", response.status_code, attempt + 1, self.max_retries, delay)
                response.close()
            attempt += 1
            time.sleep(delay)

    def _get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> tuple[Any, str | None]:
        response = self._request("GET", path, params=params)
        return response.json(), response.headers.get("x-request-id")

    def _post_json(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        idempotent: bool = False,
        idempotency_key: str | None = None,
    ) -> tuple[Any, str | None]:
        response = self._request("POST", path, json=json, idempotent=idempotent, idempotency_key=idempotency_key)
        return response.json(), response.headers.get("x-request-id")

    def _patch_json(self, path: str, *, json: Mapping[str, Any] | None = None) -> tuple[Any, str | None]:
        """A PATCH keeps explicit ``None`` values: ``{"name": None}`` clears the name."""
        response = self._request("PATCH", path, json=json, strip_none=False)
        return response.json(), response.headers.get("x-request-id")

    def _delete(self, path: str) -> None:
        self._request("DELETE", path)

    @contextlib.contextmanager
    def _stream(self, method: str, path: str, *, headers: Mapping[str, str] | None = None) -> Iterator[httpx.Response]:
        """A streamed answer (server-sent events); the response is closed on exit."""
        response = self._request(method, path, headers=headers, stream=True)
        try:
            yield response
        finally:
            response.close()

    # ----- signed URLs on other hosts ------------------------------------------------------------------------------

    def _external_request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: Callable[[], Iterable[bytes]] | None = None,
        follow_redirects: bool = False,
        stream: bool = False,
    ) -> httpx.Response:
        """A request to a signed URL without the API key, retried like an API call.

        ``content`` is called afresh for every attempt, so a PUT that failed is resent whole from its source.
        """
        http = self._external_client
        attempt = 0
        while True:
            request = http.build_request(
                method, url, headers=headers, content=content() if content is not None else None, timeout=self.timeout
            )
            try:
                response = http.send(request, stream=stream, follow_redirects=follow_redirects)
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries:
                    raise APITimeoutError(request=request) from exc
                delay = self._retry_delay(attempt, None)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise APIConnectionError(str(exc) or "Connection error.", request=request) from exc
                delay = self._retry_delay(attempt, None)
            else:
                if response.is_success:
                    return response
                if stream:
                    response.read()
                status = response.status_code
                if (status == 429 or status >= 500) and attempt < self.max_retries:
                    delay = self._retry_delay(attempt, response)
                    response.close()
                else:
                    self._raise_error(response)
            log.info(
                "%s %s failed; retry %d/%d in %.2fs", method, request.url.host, attempt + 1, self.max_retries, delay
            )
            attempt += 1
            time.sleep(delay)


class AsyncAPIClient(BaseClient):
    _http: httpx.AsyncClient

    def __init__(self, *, http_client: httpx.AsyncClient | None = None, **options: Any) -> None:
        super().__init__(**options)
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=self.timeout)

    # ----- lifecycle -----------------------------------------------------------------------------------------------

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
            await self._externals.aclose()

    async def __aenter__(self: Any) -> Any:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def _external_client(self) -> httpx.AsyncClient:
        return self._externals.async_()

    # ----- the request loop ----------------------------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool = False,
        idempotency_key: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        stream: bool = False,
        strip_none: bool = True,
    ) -> httpx.Response:
        """One API call with the retry policy applied. Raises an ``APIStatusError`` on an error answer."""
        key = self._resolve_key(idempotent=idempotent, idempotency_key=idempotency_key)
        body = self._clean_json(json, strip_none=strip_none)
        attempt = 0
        waited_in_progress = 0.0
        while True:
            request = self._http.build_request(
                method,
                self._url(path),
                params=self._clean_params(params),
                json=body,
                data=data,
                files=files,
                headers=self._headers(headers, idempotency_key=key),
                timeout=self.timeout if timeout is None else timeout,
            )
            self._log_request(request, body)
            try:
                response = await self._http.send(request, stream=stream)
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries or not self._may_resend(method, key, None):
                    raise APITimeoutError(request=request) from exc
                delay = self._retry_delay(attempt, None)
                log.info("timeout; retry %d/%d in %.2fs", attempt + 1, self.max_retries, delay)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries or not self._may_resend(method, key, None):
                    raise APIConnectionError(str(exc) or "Connection error.", request=request) from exc
                delay = self._retry_delay(attempt, None)
                log.info("connection error (%s); retry %d/%d in %.2fs", exc, attempt + 1, self.max_retries, delay)
            else:
                self._log_response(response, streamed=stream)
                if response.is_success:
                    return response
                if stream:
                    await response.aread()
                pause = self._in_progress_delay(response, key=key, waited=waited_in_progress)
                if pause is not None:
                    waited_in_progress += pause
                    log.info("idempotency_in_progress; resending in %.1fs", pause)
                    await response.aclose()
                    await anyio.sleep(pause)
                    continue
                wait = self._retry_delay_for(response, attempt, method=method, key=key)
                if wait is None:
                    self._raise_error(response)
                delay = wait
                log.info("%s; retry %d/%d in %.2fs", response.status_code, attempt + 1, self.max_retries, delay)
                await response.aclose()
            attempt += 1
            await anyio.sleep(delay)

    async def _get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> tuple[Any, str | None]:
        response = await self._request("GET", path, params=params)
        return response.json(), response.headers.get("x-request-id")

    async def _post_json(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        idempotent: bool = False,
        idempotency_key: str | None = None,
    ) -> tuple[Any, str | None]:
        response = await self._request("POST", path, json=json, idempotent=idempotent, idempotency_key=idempotency_key)
        return response.json(), response.headers.get("x-request-id")

    async def _patch_json(self, path: str, *, json: Mapping[str, Any] | None = None) -> tuple[Any, str | None]:
        """A PATCH keeps explicit ``None`` values: ``{"name": None}`` clears the name."""
        response = await self._request("PATCH", path, json=json, strip_none=False)
        return response.json(), response.headers.get("x-request-id")

    async def _delete(self, path: str) -> None:
        await self._request("DELETE", path)

    @contextlib.asynccontextmanager
    async def _stream(
        self, method: str, path: str, *, headers: Mapping[str, str] | None = None
    ) -> AsyncIterator[httpx.Response]:
        """A streamed answer (server-sent events); the response is closed on exit."""
        response = await self._request(method, path, headers=headers, stream=True)
        try:
            yield response
        finally:
            await response.aclose()

    # ----- signed URLs on other hosts ------------------------------------------------------------------------------

    async def _external_request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: Callable[[], AsyncIterable[bytes]] | None = None,
        follow_redirects: bool = False,
        stream: bool = False,
    ) -> httpx.Response:
        """A request to a signed URL without the API key, retried like an API call; see the sync twin."""
        http = self._external_client
        attempt = 0
        while True:
            request = http.build_request(
                method, url, headers=headers, content=content() if content is not None else None, timeout=self.timeout
            )
            try:
                response = await http.send(request, stream=stream, follow_redirects=follow_redirects)
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries:
                    raise APITimeoutError(request=request) from exc
                delay = self._retry_delay(attempt, None)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise APIConnectionError(str(exc) or "Connection error.", request=request) from exc
                delay = self._retry_delay(attempt, None)
            else:
                if response.is_success:
                    return response
                if stream:
                    await response.aread()
                status = response.status_code
                if (status == 429 or status >= 500) and attempt < self.max_retries:
                    delay = self._retry_delay(attempt, response)
                    await response.aclose()
                else:
                    self._raise_error(response)
            log.info(
                "%s %s failed; retry %d/%d in %.2fs", method, request.url.host, attempt + 1, self.max_retries, delay
            )
            attempt += 1
            await anyio.sleep(delay)
