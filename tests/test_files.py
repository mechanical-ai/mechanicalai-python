from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
import respx

import mechanicalai._files as files_module
from mechanicalai import APIConnectionError, AsyncClient, Client
from mechanicalai._files import guess_content_type, prepare_upload
from tests.conftest import FILE

UPLOAD_URL = "https://upload.example.test/signed?sig=abc"


def _upload_url_body() -> dict[str, object]:
    return {
        "file": {**FILE, "status": "uploading", "digest": None},
        "upload_url": UPLOAD_URL,
        "upload_headers": {"Content-Type": "model/step", "x-goog-content-length-range": "0,100"},
        "expires_at": "2026-11-06T10:12:00Z",
    }


def test_small_files_go_multipart(api: respx.MockRouter, client: Client, tmp_path: Path) -> None:
    path = tmp_path / "mounting-plate.step"
    path.write_bytes(b"ISO-10303-21;")
    route = api.post("/projects/prj_1/files").mock(return_value=httpx.Response(201, json=FILE))
    uploaded = client.files.upload("prj_1", path)
    assert uploaded.id == FILE["id"] and uploaded.status == "ready"
    request = route.calls.last.request
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'filename="mounting-plate.step"' in body
    assert b"Content-Type: model/step" in body
    assert b"ISO-10303-21;" in body
    assert b'name="name"' in body


def test_bytes_tuples_and_file_objects(api: respx.MockRouter, client: Client) -> None:
    route = api.post("/projects/prj_1/files").mock(return_value=httpx.Response(201, json=FILE))
    client.files.upload("prj_1", b"abc", name="loads.csv")
    assert b'filename="loads.csv"' in route.calls.last.request.content
    assert b"Content-Type: text/csv" in route.calls.last.request.content

    client.files.upload("prj_1", ("notes.txt", b"hello"))
    assert b'filename="notes.txt"' in route.calls.last.request.content

    stream = io.BytesIO(b"xyz")
    stream.name = "part.stp"
    client.files.upload("prj_1", stream)
    assert b'filename="part.stp"' in route.calls.last.request.content
    assert b"Content-Type: model/step" in route.calls.last.request.content

    client.files.upload("prj_1", io.BytesIO(b"xyz"), name="given.bin", content_type="application/x-thing")
    assert b"Content-Type: application/x-thing" in route.calls.last.request.content


def test_inputs_are_checked() -> None:
    with pytest.raises(ValueError, match="name"):
        prepare_upload(b"abc")
    with pytest.raises(FileNotFoundError):
        prepare_upload("/no/such/file.step")
    with pytest.raises(TypeError):
        prepare_upload(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="binary"):
        prepare_upload(io.StringIO("text"), name="x.txt")  # type: ignore[arg-type]


def test_content_types() -> None:
    assert guess_content_type("a.STEP") == "model/step"
    assert guess_content_type("a.dxf") == "image/vnd.dxf"
    assert guess_content_type("a.unknownext") == "application/octet-stream"
    source = prepare_upload(b"abc", name="dir/x.csv")
    assert source.name == "x.csv"
    assert source.digest() == "sha256:" + hashlib.sha256(b"abc").hexdigest()
    assert source.size == 3


def test_large_files_use_an_upload_url(
    api: respx.MockRouter, client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files_module, "MULTIPART_MAX_BYTES", 16)
    data = bytes(range(100))
    path = tmp_path / "big.step"
    path.write_bytes(data)
    multipart = api.post("/projects/prj_1/files")
    url_route = api.post("/projects/prj_1/files/upload-url").mock(
        return_value=httpx.Response(201, json=_upload_url_body())
    )
    put_route = api.put(UPLOAD_URL).mock(return_value=httpx.Response(200))
    complete = api.post(f"/files/{FILE['id']}/complete").mock(return_value=httpx.Response(200, json=FILE))

    uploaded = client.files.upload("prj_1", path, idempotency_key="upload-1")

    assert uploaded.status == "ready"
    assert multipart.call_count == 0
    assert json.loads(url_route.calls.last.request.content) == {
        "name": "big.step",
        "size": 100,
        "content_type": "model/step",
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    assert url_route.calls.last.request.headers["idempotency-key"] == "upload-1"
    put = put_route.calls.last.request
    assert put.content == data
    assert put.headers["content-type"] == "model/step"
    assert put.headers["x-goog-content-length-range"] == "0,100"
    assert put.headers["content-length"] == "100"
    assert "authorization" not in put.headers
    assert complete.call_count == 1


def test_download_writes_the_file(api: respx.MockRouter, client: Client, tmp_path: Path) -> None:
    api.get(f"/files/{FILE['id']}").mock(return_value=httpx.Response(200, json=FILE))
    api.get(f"/files/{FILE['id']}/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "https://media.example.test/f?sig=1",
                "expires_at": "2026-11-06T10:25:00Z",
                "content_type": "model/step",
                "size": 5,
            },
        )
    )
    media = api.get("https://media.example.test/f").mock(return_value=httpx.Response(200, content=b"bytes"))
    target = client.files.download(FILE["id"], tmp_path)
    assert target == tmp_path / "mounting-plate.step"
    assert target.read_bytes() == b"bytes"
    assert "authorization" not in media.calls.last.request.headers


def test_list_retrieve_delete(api: respx.MockRouter, client: Client) -> None:
    api.get("/projects/prj_1/files").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [FILE], "has_more": False, "next_cursor": None}
        )
    )
    api.get(f"/files/{FILE['id']}").mock(return_value=httpx.Response(200, json=FILE))
    delete = api.delete(f"/files/{FILE['id']}").mock(return_value=httpx.Response(204))
    assert [f.id for f in client.files.list("prj_1")] == [FILE["id"]]
    assert client.files.retrieve(FILE["id"]).name == "mounting-plate.step"
    client.files.delete(FILE["id"])
    assert delete.call_count == 1


async def test_async_small_upload(api: respx.MockRouter, async_client: AsyncClient) -> None:
    route = api.post("/projects/prj_1/files").mock(return_value=httpx.Response(201, json=FILE))
    uploaded = await async_client.files.upload("prj_1", b"abc", name="loads.csv")
    assert uploaded.status == "ready"
    assert b'filename="loads.csv"' in route.calls.last.request.content


async def test_async_large_upload(
    api: respx.MockRouter, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files_module, "MULTIPART_MAX_BYTES", 2)
    data = b"0123456789"
    api.post("/projects/prj_1/files/upload-url").mock(return_value=httpx.Response(201, json=_upload_url_body()))
    put_route = api.put(UPLOAD_URL).mock(return_value=httpx.Response(200))
    api.post(f"/files/{FILE['id']}/complete").mock(return_value=httpx.Response(200, json=FILE))
    uploaded = await async_client.files.upload("prj_1", data, name="big.step")
    assert uploaded.status == "ready"
    assert put_route.calls.last.request.content == data
    assert "authorization" not in put_route.calls.last.request.headers


async def test_async_download(api: respx.MockRouter, async_client: AsyncClient, tmp_path: Path) -> None:
    api.get(f"/files/{FILE['id']}/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "https://media.example.test/f",
                "expires_at": "2026-11-06T10:25:00Z",
                "content_type": "model/step",
                "size": 5,
            },
        )
    )
    api.get("https://media.example.test/f").mock(return_value=httpx.Response(200, content=b"bytes"))
    from mechanicalai.types import File

    target = await async_client.files.download(File.model_validate(FILE), tmp_path / "plate.step")
    assert target.read_bytes() == b"bytes"


def test_a_real_file_object_streams_from_its_current_position(tmp_path: Path) -> None:
    path = tmp_path / "plate.step"
    path.write_bytes(b"HEADER;" + bytes(range(50)))
    with open(path, "rb") as stream:
        stream.seek(7)
        source = prepare_upload(stream)
        assert source._stream is stream and source._data is None
        assert source.size == 50
        assert source.digest() == "sha256:" + hashlib.sha256(bytes(range(50))).hexdigest()
        assert b"".join(source.iter_bytes(chunk_size=8)) == bytes(range(50))
        assert source.read_bytes() == bytes(range(50))


def test_multipart_uploads_do_not_hash(api: respx.MockRouter, client: Client, tmp_path: Path) -> None:
    api.post("/projects/prj_1/files").mock(return_value=httpx.Response(201, json=FILE))
    source = prepare_upload(b"abc", name="x.csv")
    client.files._upload_multipart("prj_1", source, None)
    assert source._digest is None


def test_a_413_falls_through_to_the_upload_url(api: respx.MockRouter, client: Client) -> None:
    multipart = api.post("/projects/prj_1/files").mock(
        return_value=httpx.Response(
            413,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "file_too_large",
                    "message": "Too big.",
                    "param": "file",
                    "request_id": "r",
                }
            },
        )
    )
    url_route = api.post("/projects/prj_1/files/upload-url").mock(
        return_value=httpx.Response(201, json=_upload_url_body())
    )
    put_route = api.put(UPLOAD_URL).mock(return_value=httpx.Response(200))
    api.post(f"/files/{FILE['id']}/complete").mock(return_value=httpx.Response(200, json=FILE))
    uploaded = client.files.upload("prj_1", b"0123456789", name="big.step")
    assert uploaded.status == "ready"
    assert multipart.call_count == 1 and url_route.call_count == 1
    assert put_route.calls.last.request.content == b"0123456789"
    assert (
        json.loads(url_route.calls.last.request.content)["digest"]
        == "sha256:" + hashlib.sha256(b"0123456789").hexdigest()
    )


def test_the_put_is_resent_whole_after_a_connection_error(
    api: respx.MockRouter, client: Client, monkeypatch: pytest.MonkeyPatch, sleeps: list[float], tmp_path: Path
) -> None:
    monkeypatch.setattr(files_module, "MULTIPART_MAX_BYTES", 2)
    path = tmp_path / "big.step"
    path.write_bytes(b"0123456789")
    api.post("/projects/prj_1/files/upload-url").mock(return_value=httpx.Response(201, json=_upload_url_body()))
    put_route = api.put(UPLOAD_URL).mock(
        side_effect=[httpx.ConnectError("reset"), httpx.Response(503), httpx.Response(200)]
    )
    api.post(f"/files/{FILE['id']}/complete").mock(return_value=httpx.Response(200, json=FILE))
    client.files.upload("prj_1", path)
    assert put_route.call_count == 3
    assert all(c.request.content == b"0123456789" for c in put_route.calls if c.request.content)
    assert put_route.calls.last.request.content == b"0123456789"
    assert len(sleeps) == 2


def test_the_put_gives_up_like_an_api_call(
    api: respx.MockRouter, client: Client, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setattr(files_module, "MULTIPART_MAX_BYTES", 2)
    api.post("/projects/prj_1/files/upload-url").mock(return_value=httpx.Response(201, json=_upload_url_body()))
    put_route = api.put(UPLOAD_URL).mock(side_effect=httpx.ConnectError("reset"))
    with pytest.raises(APIConnectionError):
        client.files.upload("prj_1", b"0123456789", name="big.step")
    assert put_route.call_count == 3


def test_the_put_does_not_follow_redirects(
    api: respx.MockRouter, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files_module, "MULTIPART_MAX_BYTES", 2)
    api.post("/projects/prj_1/files/upload-url").mock(return_value=httpx.Response(201, json=_upload_url_body()))
    api.put(UPLOAD_URL).mock(return_value=httpx.Response(307, headers={"Location": "https://elsewhere.example.test/"}))
    elsewhere = api.put("https://elsewhere.example.test/")
    with pytest.raises(Exception) as info:
        client.files.upload("prj_1", b"0123456789", name="big.step")
    assert elsewhere.call_count == 0
    assert "307" in str(info.value)


def test_the_download_get_is_retried_on_503(
    api: respx.MockRouter, client: Client, tmp_path: Path, sleeps: list[float]
) -> None:
    api.get(f"/files/{FILE['id']}/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "https://media.example.test/f",
                "expires_at": "2026-11-06T10:25:00Z",
                "content_type": "model/step",
                "size": 5,
            },
        )
    )
    media = api.get("https://media.example.test/f").mock(
        side_effect=[httpx.Response(503), httpx.ConnectError("reset"), httpx.Response(200, content=b"bytes")]
    )
    from mechanicalai.types import File

    target = client.files.download(File.model_validate(FILE), tmp_path)
    assert target.read_bytes() == b"bytes"
    assert media.call_count == 3
    assert len(sleeps) == 2


async def test_async_file_object_upload_reads_in_a_thread(
    api: respx.MockRouter, async_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files_module, "MULTIPART_MAX_BYTES", 2)
    path = tmp_path / "big.step"
    path.write_bytes(b"xx0123456789")
    api.post("/projects/prj_1/files/upload-url").mock(return_value=httpx.Response(201, json=_upload_url_body()))
    put_route = api.put(UPLOAD_URL).mock(return_value=httpx.Response(200))
    api.post(f"/files/{FILE['id']}/complete").mock(return_value=httpx.Response(200, json=FILE))
    with open(path, "rb") as stream:
        stream.seek(2)
        await async_client.files.upload("prj_1", stream)
    assert put_route.calls.last.request.content == b"0123456789"
    assert put_route.calls.last.request.headers["content-length"] == "10"


def test_a_file_above_the_digest_bound_declares_no_digest(
    api: respx.MockRouter, client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files_module, "MULTIPART_MAX_BYTES", 16)
    monkeypatch.setattr(files_module, "DIGEST_MAX_BYTES", 16)
    data = bytes(range(100))
    path = tmp_path / "big.step"
    path.write_bytes(data)
    url_route = api.post("/projects/prj_1/files/upload-url").mock(
        return_value=httpx.Response(201, json=_upload_url_body())
    )
    api.put(UPLOAD_URL).mock(return_value=httpx.Response(200))
    api.post(f"/files/{FILE['id']}/complete").mock(return_value=httpx.Response(200, json=FILE))

    uploaded = client.files.upload("prj_1", path)

    assert uploaded.status == "ready"
    assert json.loads(url_route.calls.last.request.content).get("digest") is None
