from __future__ import annotations

import logging

import httpx
import pytest
import respx

import mechanicalai
from mechanicalai import AsyncClient, Client
from tests.conftest import API_KEY, BASE_URL, PROJECT, list_body


def test_bearer_auth_user_agent_and_accept(api: respx.MockRouter, client: Client) -> None:
    route = api.get("/engines").mock(return_value=httpx.Response(200, json=list_body([])))
    client.engines.list()
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["user-agent"].startswith(f"mechanicalai-python/{mechanicalai.__version__} python/3.")
    assert " httpx/" in request.headers["user-agent"]
    assert request.headers["accept"] == "application/json"


def test_request_id_is_sent_and_read_back(api: respx.MockRouter, client: Client) -> None:
    route = api.get("/projects/prj_1").mock(
        return_value=httpx.Response(200, json=PROJECT, headers={"X-Request-Id": "req_from_server"})
    )
    project = client.with_options(default_headers={"X-Request-Id": "req_mine"}).projects.retrieve("prj_1")
    assert route.calls.last.request.headers["x-request-id"] == "req_mine"
    assert project.request_id == "req_from_server"


def test_default_headers_and_with_options_share_the_pool(api: respx.MockRouter, client: Client) -> None:
    route = api.get("/engines").mock(return_value=httpx.Response(200, json=list_body([])))
    other = client.with_options(default_headers={"X-Team": "line-3"}, timeout=5, max_retries=0)
    other.engines.list()
    assert route.calls.last.request.headers["x-team"] == "line-3"
    assert other.timeout == 5
    assert other.max_retries == 0
    assert other.api_key == client.api_key
    assert other._http is client._http


def test_missing_key_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MECHANICALAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MECHANICALAI_API_KEY"):
        Client()


def test_key_and_base_url_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MECHANICALAI_API_KEY", API_KEY)
    monkeypatch.setenv("MECHANICALAI_BASE_URL", "https://api.example.test/v1/")
    client = Client()
    assert client.api_key == API_KEY
    assert client.base_url == "https://api.example.test/v1"
    monkeypatch.delenv("MECHANICALAI_BASE_URL")
    assert Client().base_url == "https://api.mechanicalai.com/v1"
    assert mechanicalai.DEFAULT_BASE_URL == "https://api.mechanicalai.com/v1"


def test_defaults() -> None:
    client = Client(api_key=API_KEY)
    assert client.timeout == httpx.Timeout(60.0, connect=5.0)
    assert client.max_retries == 2
    assert client.default_headers == {}
    client.close()


def test_query_params_drop_none_and_spell_booleans(api: respx.MockRouter, client: Client) -> None:
    route = api.get("/projects").mock(return_value=httpx.Response(200, json=list_body([])))
    client.projects.list(limit=5, after=None)
    assert dict(route.calls.last.request.url.params) == {"limit": "5"}
    assert client._clean_params({"flag": True, "x": None}) == {"flag": "true"}


def test_json_bodies_drop_none(api: respx.MockRouter, client: Client) -> None:
    route = api.post("/projects").mock(return_value=httpx.Response(201, json=PROJECT))
    client.projects.create(name="Motor bracket, line 3")
    import json

    assert json.loads(route.calls.last.request.content) == {"name": "Motor bracket, line 3"}


def test_context_manager_closes_the_pool() -> None:
    with Client(api_key=API_KEY) as client:
        http = client._http
    assert http.is_closed


def test_log_level_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MECHANICALAI_LOG", "debug")
    Client(api_key=API_KEY).close()
    assert logging.getLogger("mechanicalai").level == logging.DEBUG


def test_debug_logging_redacts_the_key(api: respx.MockRouter, client: Client, caplog: pytest.LogCaptureFixture) -> None:
    api.get("/engines").mock(return_value=httpx.Response(200, json=list_body([])))
    with caplog.at_level(logging.DEBUG, logger="mechanicalai"):
        client.engines.list()
    text = caplog.text
    assert "[redacted]" in text
    assert API_KEY not in text
    assert f"GET {BASE_URL}/engines" in text


async def test_async_client_sends_the_same_headers(api: respx.MockRouter, async_client: AsyncClient) -> None:
    route = api.get("/engines").mock(return_value=httpx.Response(200, json=list_body([])))
    await async_client.engines.list()
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["user-agent"].startswith("mechanicalai-python/")


async def test_async_context_manager_closes_the_pool() -> None:
    async with AsyncClient(api_key=API_KEY) as client:
        http = client._http
    assert http.is_closed


def test_api_key_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        Client(API_KEY)  # type: ignore[misc]


def test_debug_logging_redacts_signed_urls(
    api: respx.MockRouter, client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    api.get("/artifacts/art_1/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "https://storage.example.test/b/o?X-Goog-Algorithm=GOOG4&X-Goog-Signature=0123abcd&Expires=99",
                "expires_at": "2026-11-06T10:25:00Z",
                "format": "step",
                "content_type": "model/step",
                "size": 1,
            },
        )
    )
    with caplog.at_level(logging.DEBUG, logger="mechanicalai"):
        client.artifacts.download_url("art_1")
    assert "0123abcd" not in caplog.text
    assert "X-Goog-Signature=[redacted]" in caplog.text
    assert "Expires=[redacted]" in caplog.text


def test_external_client_is_shared_with_options_copies(client: Client) -> None:
    other = client.with_options(max_retries=0)
    assert other._externals is client._externals
    assert other._external_client is client._external_client
    assert "authorization" not in client._external_client.headers


def test_the_public_surface_hides_the_transport(client: Client) -> None:
    public = {name for name in dir(client) if not name.startswith("_")}
    assert public >= {"engines", "projects", "files", "runs", "artifacts", "usage", "with_options", "close"}
    assert not public & {"request", "get_json", "post_json", "patch_json", "delete", "stream", "external"}
