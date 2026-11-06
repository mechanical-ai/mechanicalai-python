from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from mechanicalai import APIStatusError, AsyncClient, Client, WaitTimeoutError
from mechanicalai.types import Artifact
from tests.conftest import ARTIFACT_MODEL, ARTIFACT_REPORT, list_body

MEDIA = "https://media.example.test/art"


def _link(fmt: str, content_type: str = "application/octet-stream") -> dict[str, object]:
    return {
        "url": f"{MEDIA}/{fmt}?sig=1",
        "expires_at": "2026-11-06T10:25:00Z",
        "format": fmt,
        "content_type": content_type,
        "size": 5,
    }


def test_download_into_a_directory_names_the_file(api: respx.MockRouter, client: Client, tmp_path: Path) -> None:
    api.get("/artifacts/art_01jc5202").mock(return_value=httpx.Response(200, json=ARTIFACT_MODEL))
    link = api.get("/artifacts/art_01jc5202/download").mock(return_value=httpx.Response(200, json=_link("step")))
    media = api.get(f"{MEDIA}/step").mock(return_value=httpx.Response(200, content=b"solid"))
    out = tmp_path / "out"
    target = client.artifacts.download("art_01jc5202", out)
    assert target == out / "bracket.step"
    assert target.read_bytes() == b"solid"
    assert "format" not in link.calls.last.request.url.params
    assert "authorization" not in media.calls.last.request.headers
    assert not list(out.glob("*.part"))


def test_download_in_a_format_and_to_an_exact_path(api: respx.MockRouter, client: Client, tmp_path: Path) -> None:
    link = api.get("/artifacts/art_01jc5202/download").mock(return_value=httpx.Response(200, json=_link("stl")))
    api.get(f"{MEDIA}/stl").mock(return_value=httpx.Response(200, content=b"facet"))
    artifact = Artifact.model_validate(ARTIFACT_MODEL)
    target = client.artifacts.download(artifact, tmp_path / "parts" / "bracket-v2.stl", format="stl")
    assert link.calls.last.request.url.params["format"] == "stl"
    assert target == tmp_path / "parts" / "bracket-v2.stl"
    assert target.read_bytes() == b"facet"
    into_dir = client.artifacts.download(artifact, tmp_path, format="stl")
    assert into_dir == tmp_path / "bracket.stl"


def test_a_missing_directory_without_an_extension_is_created(
    api: respx.MockRouter, client: Client, tmp_path: Path
) -> None:
    api.get("/artifacts/art_01jc5202/download").mock(return_value=httpx.Response(200, json=_link("step")))
    api.get("/artifacts/art_01jc5201/download").mock(return_value=httpx.Response(200, json=_link("md")))
    api.get(f"{MEDIA}/step").mock(return_value=httpx.Response(200, content=b"solid"))
    api.get(f"{MEDIA}/md").mock(return_value=httpx.Response(200, content=b"# report"))
    first = client.artifacts.download(Artifact.model_validate(ARTIFACT_MODEL), tmp_path / "out")
    second = client.artifacts.download(Artifact.model_validate(ARTIFACT_REPORT), tmp_path / "out")
    assert first == tmp_path / "out" / "bracket.step"
    assert second == tmp_path / "out" / "bracket-report.md"
    assert (tmp_path / "out").is_dir()


def test_a_name_that_already_has_the_extension_is_kept(api: respx.MockRouter, client: Client, tmp_path: Path) -> None:
    api.get("/artifacts/art_01jc5202/download").mock(return_value=httpx.Response(200, json=_link("step")))
    api.get(f"{MEDIA}/step").mock(return_value=httpx.Response(200, content=b"solid"))
    artifact = Artifact.model_validate({**ARTIFACT_MODEL, "name": "bracket.step"})
    assert client.artifacts.download(artifact, tmp_path).name == "bracket.step"


def test_a_failed_transfer_leaves_no_file(api: respx.MockRouter, client: Client, tmp_path: Path) -> None:
    api.get("/artifacts/art_01jc5202/download").mock(return_value=httpx.Response(200, json=_link("step")))
    api.get(f"{MEDIA}/step").mock(return_value=httpx.Response(403, text="expired"))
    with pytest.raises(APIStatusError) as info:
        client.artifacts.download(Artifact.model_validate(ARTIFACT_MODEL), tmp_path)
    assert info.value.status_code == 403
    assert list(tmp_path.iterdir()) == []


def test_download_waits_while_a_format_is_composed(
    api: respx.MockRouter, client: Client, tmp_path: Path, sleeps: list[float]
) -> None:
    link = api.get("/artifacts/art_01jc5202/download").mock(
        side_effect=[
            httpx.Response(202, json={"status": "preparing", "retry_after": 5}, headers={"Retry-After": "3"}),
            httpx.Response(202, json={"status": "preparing", "retry_after": 1}),
            httpx.Response(200, json=_link("glb")),
        ]
    )
    api.get(f"{MEDIA}/glb").mock(return_value=httpx.Response(200, content=b"glTF"))
    target = client.artifacts.download(Artifact.model_validate(ARTIFACT_MODEL), tmp_path, format="glb")
    assert target.read_bytes() == b"glTF"
    assert link.call_count == 3
    assert sleeps == [3.0, 1.0]


def test_download_gives_up_after_the_prepare_timeout(
    api: respx.MockRouter, client: Client, sleeps: list[float]
) -> None:
    api.get("/artifacts/art_01jc5202/download").mock(
        return_value=httpx.Response(202, json={"status": "preparing", "retry_after": 5})
    )
    with pytest.raises(WaitTimeoutError, match="still being composed"):
        client.artifacts.download_url("art_01jc5202", format="glb", timeout=3)
    assert sleeps == []


async def test_async_download_url_waits_too(
    api: respx.MockRouter, async_client: AsyncClient, sleeps: list[float]
) -> None:
    api.get("/artifacts/art_01jc5202/download").mock(
        side_effect=[httpx.Response(202, json={"status": "preparing"}), httpx.Response(200, json=_link("stl"))]
    )
    link = await async_client.artifacts.download_url("art_01jc5202", format="stl")
    assert link.format == "stl"
    assert sleeps == [5.0]


def test_lists(api: respx.MockRouter, client: Client) -> None:
    by_run = api.get("/runs/run_1/artifacts").mock(
        return_value=httpx.Response(200, json=list_body([ARTIFACT_REPORT, ARTIFACT_MODEL]))
    )
    by_project = api.get("/projects/prj_1/artifacts").mock(
        side_effect=[
            httpx.Response(200, json=list_body([ARTIFACT_REPORT], has_more=True, next_cursor="art_01jc5201")),
            httpx.Response(200, json=list_body([ARTIFACT_MODEL])),
        ]
    )
    assert [a.kind for a in client.artifacts.list_for_run("run_1", kind="model")] == ["report", "model"]
    assert by_run.calls.last.request.url.params["kind"] == "model"
    names = [a.name for a in client.artifacts.list_for_project("prj_1", run_id="run_1", limit=1)]
    assert names == ["bracket-report", "bracket"]
    assert dict(by_project.calls[1].request.url.params) == {"run_id": "run_1", "limit": "1", "after": "art_01jc5201"}


def test_retrieve_and_delete(api: respx.MockRouter, client: Client) -> None:
    api.get("/artifacts/art_01jc5201").mock(return_value=httpx.Response(200, json=ARTIFACT_REPORT))
    delete = api.delete("/artifacts/art_01jc5201").mock(return_value=httpx.Response(204))
    artifact = client.artifacts.retrieve("art_01jc5201")
    assert artifact.formats == ["md", "pdf"] and artifact.default_format == "md"
    client.artifacts.delete("art_01jc5201")
    assert delete.call_count == 1


async def test_async_download(api: respx.MockRouter, async_client: AsyncClient, tmp_path: Path) -> None:
    api.get("/artifacts/art_01jc5201").mock(return_value=httpx.Response(200, json=ARTIFACT_REPORT))
    api.get("/artifacts/art_01jc5201/download").mock(return_value=httpx.Response(200, json=_link("pdf")))
    api.get(f"{MEDIA}/pdf").mock(return_value=httpx.Response(200, content=b"%PDF"))
    target = await async_client.artifacts.download("art_01jc5201", tmp_path, format="pdf")
    assert target == tmp_path / "bracket-report.pdf"
    assert target.read_bytes() == b"%PDF"
    api.get("/runs/run_1/artifacts").mock(return_value=httpx.Response(200, json=list_body([ARTIFACT_REPORT])))
    page = await async_client.artifacts.list_for_run("run_1")
    assert [a.id async for a in page] == ["art_01jc5201"]
    assert [a.id for a in page.data] == ["art_01jc5201"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("bracket", "bracket.step"),
        ("../../etc/passwd", "passwd.step"),
        ("C:\\plant\\bracket v2.", "bracket v2.step"),
        ("con", "_con.step"),
        ("nul.step", "_nul.step"),
        ("line\x00break\x1f.step", "linebreak.step"),
        ('what?*<>|:"', "what_______.step"),
        ("   ", "download.step"),
    ],
)
def test_download_names_are_made_safe(
    api: respx.MockRouter, client: Client, tmp_path: Path, name: str, expected: str
) -> None:
    api.get("/artifacts/art_01jc5202/download").mock(return_value=httpx.Response(200, json=_link("step")))
    api.get(f"{MEDIA}/step").mock(return_value=httpx.Response(200, content=b"solid"))
    artifact = Artifact.model_validate({**ARTIFACT_MODEL, "name": name})
    target = client.artifacts.download(artifact, tmp_path)
    assert target == tmp_path / expected
    assert target.read_bytes() == b"solid"
    assert sorted(p.name for p in tmp_path.iterdir()) == [expected]


def test_list_for_run_continues_through_the_project_route_when_paged(api: respx.MockRouter, client: Client) -> None:
    api.get("/runs/run_1/artifacts").mock(
        return_value=httpx.Response(200, json=list_body([ARTIFACT_REPORT], has_more=True, next_cursor="art_01jc5201"))
    )
    by_project = api.get(f"/projects/{ARTIFACT_REPORT['project_id']}/artifacts").mock(
        return_value=httpx.Response(200, json=list_body([ARTIFACT_MODEL]))
    )
    names = [a.name for a in client.artifacts.list_for_run("run_1", kind="model")]
    assert names == ["bracket-report", "bracket"]
    assert dict(by_project.calls.last.request.url.params) == {
        "kind": "model",
        "run_id": "run_1",
        "after": "art_01jc5201",
    }


def test_unknown_artifact_kinds_parse(api: respx.MockRouter, client: Client) -> None:
    api.get("/artifacts/art_9").mock(
        return_value=httpx.Response(200, json={**ARTIFACT_MODEL, "id": "art_9", "kind": "hologram"})
    )
    assert client.artifacts.retrieve("art_9").kind == "hologram"
