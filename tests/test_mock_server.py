"""The README quickstart against a Prism mock of the contract.

Prism (``npx -y @stoplight/prism-cli@5 mock spec/public-api.openapi.yaml``) answers every route from the contract's
examples, so this checks the library's wire shapes against the contract rather than exact values. It needs ``npx``;
without it, or when Prism does not come up, the module skips.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

import mechanicalai
from mechanicalai.types import Artifact, File, Project, Run, RunEvent

SPEC = Path(__file__).resolve().parents[1] / "spec" / "public-api.openapi.yaml"
FAKE_KEY = "mk_0000000000000000000000000000000000000000"
PREFERRED_PORT = 4010
STARTUP_TIMEOUT_S = 120


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            probe.bind(("127.0.0.1", 0))
            port: int = probe.getsockname()[1]
            return port


@pytest.fixture(scope="module")
def prism() -> Iterator[str]:
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx is not on the PATH; Prism cannot start.")
    if os.environ.get("MECHANICALAI_SKIP_PRISM"):
        pytest.skip("MECHANICALAI_SKIP_PRISM is set.")
    port = _free_port(PREFERRED_PORT)
    log = open(Path(__file__).parent / ".prism.log", "w")  # noqa: SIM115
    process = subprocess.Popen(
        [npx, "-y", "@stoplight/prism-cli@5", "mock", str(SPEC), "-p", str(port), "-h", "127.0.0.1"],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    ready = False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                response = httpx.get(f"{base_url}/engines", headers={"Authorization": f"Bearer {FAKE_KEY}"}, timeout=2)
                if response.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        if not ready:
            _stop(process)
            log.close()
            tail = (Path(__file__).parent / ".prism.log").read_text()[-2000:]
            pytest.skip(f"Prism did not come up on {base_url} within {STARTUP_TIMEOUT_S} s:\n{tail}")
        yield base_url
    finally:
        _stop(process)
        log.close()
        (Path(__file__).parent / ".prism.log").unlink(missing_ok=True)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


@pytest.fixture
def media_stub() -> Iterator[respx.MockRouter]:
    """Prism hands out example download links on a real host: serve those bytes here, pass the rest through."""
    with respx.mock(assert_all_called=False) as router:
        router.route(host="127.0.0.1").pass_through()
        router.route(host="localhost").pass_through()
        router.route().mock(return_value=httpx.Response(200, content=b"example bytes"))
        yield router


def test_quickstart_against_prism(
    prism: str, media_stub: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("mounting-plate.step").write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")

    client = mechanicalai.Client(api_key=FAKE_KEY, base_url=prism)
    project = client.projects.create(name="Motor bracket, line 3")
    assert isinstance(project, Project) and project.id.startswith("prj_")

    plate = client.files.upload(project.id, "mounting-plate.step")
    assert isinstance(plate, File) and plate.id.startswith("file_")
    assert plate.status in ("ready", "uploading")

    def follow(run: Run) -> Run:
        events = list(client.runs.stream(run.id))
        assert events and all(isinstance(e, RunEvent) for e in events)
        assert events[-1].kind == "status" and events[-1].status in ("succeeded", "needs_input", "failed", "cancelled")
        return client.runs.retrieve(run.id)

    run = client.runs.create(
        project.id,
        engine="mekint",
        files=[plate.id],
        problem="Design a steel bracket that mounts the 12 kg motor on the attached plate using the four M8 holes. "
        "Keep it under 1.2 kg and the first mode above 80 Hz. Check static stress at 3x load.",
    )
    assert isinstance(run, Run) and run.id.startswith("run_")
    assert run.status in ("queued", "running", "needs_input", "succeeded", "failed", "cancelled")
    assert "budget_cents" not in run.to_dict() and "report_artifact_id" not in run.to_dict()

    run = follow(run)
    while run.status == "needs_input":  # the engine asked, as it does in the app: answer it
        run = follow(client.runs.reply(run.id, text="Steel, 10 mm."))
    assert isinstance(run.cost_cents, int)
    assert run.summary is None or isinstance(run.summary, str)

    artifacts = list(client.artifacts.list_for_run(run.id))
    assert artifacts and all(isinstance(a, Artifact) for a in artifacts)
    # The README passes ``artifact.id``; here the object goes in, because ``GET /artifacts/{id}`` has no example in
    # the contract and Prism synthesises an artifact whose id is the literal "string".
    written = [client.artifacts.download(artifact, "./out") for artifact in artifacts]
    assert all(path.is_file() for path in written)
    assert all(path.parent == Path("out") for path in written)
    assert len({path.name for path in written}) == len(artifacts)
    link = client.artifacts.download_url(artifacts[0].id)
    assert link.url.startswith("https://") and link.content_type

    page = client.artifacts.list_for_project(project.id)
    assert isinstance(page.has_more, bool)
    limits = client.usage.limits()
    assert limits.projects_per_user > 0 and limits.user_char_limit > 0
    usage = client.usage.retrieve(period="month")
    assert usage.period in ("day", "month")
    engines = client.engines.list()
    assert engines.data and engines.data[0].id
    client.close()


def test_prism_refuses_a_missing_key(prism: str) -> None:
    response = httpx.get(f"{prism}/engines")
    assert response.status_code == 401


async def test_async_quickstart_against_prism(prism: str, media_stub: respx.MockRouter, tmp_path: Path) -> None:
    async with mechanicalai.AsyncClient(api_key=FAKE_KEY, base_url=prism) as client:
        project = await client.projects.create(name="Motor bracket, line 3")
        plate = await client.files.upload(project.id, b"ISO-10303-21;", name="mounting-plate.step")
        run = await client.runs.create(project.id, engine="mekint", files=[plate.id], problem="A bracket.")
        events = [e async for e in client.runs.stream(run.id)]
        assert events and events[-1].kind == "status"
        artifacts = await client.artifacts.list_for_run(run.id)
        async for artifact in artifacts:
            path = await client.artifacts.download(artifact, tmp_path)
            assert path.is_file()
