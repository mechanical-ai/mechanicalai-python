from __future__ import annotations

import httpx
import respx

from mechanicalai import AsyncClient, Client
from mechanicalai.pagination import SyncPage
from mechanicalai.types import Project
from tests.conftest import EVENTS, PROJECT, list_body


def _project(i: int) -> dict[str, object]:
    return {**PROJECT, "id": f"prj_{i}"}


def _three_pages(api: respx.MockRouter) -> respx.Route:
    def answer(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(200, json=list_body([_project(1), _project(2)], has_more=True, next_cursor="prj_2"))
        if after == "prj_2":
            return httpx.Response(200, json=list_body([_project(3), _project(4)], has_more=True, next_cursor="prj_4"))
        assert after == "prj_4"
        return httpx.Response(200, json=list_body([_project(5)], has_more=False, next_cursor=None))

    return api.get("/projects").mock(side_effect=answer)


def test_iteration_walks_every_page(api: respx.MockRouter, client: Client) -> None:
    route = _three_pages(api)
    page = client.projects.list(limit=2)
    assert [p.id for p in page.data] == ["prj_1", "prj_2"]
    assert page.has_more and page.next_cursor == "prj_2"
    assert len(page.data) == 2 and page.data[0].id == "prj_1"

    ids = [p.id for p in page]
    assert ids == ["prj_1", "prj_2", "prj_3", "prj_4", "prj_5"]
    assert route.call_count == 3
    assert all(call.request.url.params["limit"] == "2" for call in route.calls)
    assert [c.request.url.params.get("after") for c in route.calls] == [None, "prj_2", "prj_4"]


def test_next_page_is_none_at_the_end(api: respx.MockRouter, client: Client) -> None:
    api.get("/projects").mock(return_value=httpx.Response(200, json=list_body([_project(1)], has_more=False)))
    page = client.projects.list()
    assert page.next_page() is None
    assert list(page) == page.data


def test_a_repeated_cursor_ends_iteration(api: respx.MockRouter, client: Client) -> None:
    api.get("/projects").mock(
        return_value=httpx.Response(200, json=list_body([_project(1)], has_more=True, next_cursor="prj_1"))
    )
    assert len(list(client.projects.list())) == 2


def test_event_pages_resume_from_the_sequence_cursor(api: respx.MockRouter, client: Client) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(200, json=list_body(EVENTS[:2], has_more=True, next_cursor="2"))
        assert after == "2"
        return httpx.Response(200, json=list_body(EVENTS[2:], has_more=False))

    route = api.get("/runs/run_1/events").mock(side_effect=answer)
    seqs = [e.seq for e in client.runs.events.list("run_1")]
    assert seqs == [1, 2, 3, 4, 5]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["after"] == "2"


def test_a_page_keeps_the_request_id_and_builds_from_a_body() -> None:
    page = SyncPage.from_body(list_body([_project(1)]), Project, request_id="req_x")
    assert page.request_id == "req_x"
    assert page.data[0].id == "prj_1"
    assert "prj_1" in repr(page)


async def test_async_iteration_walks_every_page(api: respx.MockRouter, async_client: AsyncClient) -> None:
    route = _three_pages(api)
    page = await async_client.projects.list(limit=2)
    ids = [p.id async for p in page]
    assert ids == ["prj_1", "prj_2", "prj_3", "prj_4", "prj_5"]
    assert route.call_count == 3
    assert [p.id for p in page.data] == ["prj_1", "prj_2"]
    following = await page.next_page()
    assert following is not None and following.data[0].id == "prj_3"
