from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

import mechanicalai
from mechanicalai._cli import main
from tests.conftest import (
    API_KEY,
    ARTIFACT_MODEL,
    ARTIFACT_REPORT,
    BASE_URL,
    EVENTS,
    FILE,
    LIMITS,
    PROJECT,
    RUN_QUEUED,
    RUN_SUCCEEDED,
    USAGE,
    error_body,
    list_body,
    sse_response,
    sse_text,
)

AUTH = ["--api-key", API_KEY, "--base-url", BASE_URL]
MEDIA = "https://media.example.test/a"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == f"mechanicalai {mechanicalai.__version__}"
    assert mechanicalai.__version__ == "0.0.2"


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "solve" in capsys.readouterr().out


def test_missing_key_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("MECHANICALAI_API_KEY", raising=False)
    assert main(["limits"]) == 2
    assert "MECHANICALAI_API_KEY" in capsys.readouterr().err


def test_solve_end_to_end(api: respx.MockRouter, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plate = tmp_path / "mounting-plate.step"
    plate.write_bytes(b"ISO-10303-21;")
    out = tmp_path / "out"

    create_project = api.post("/projects").mock(return_value=httpx.Response(201, json=PROJECT))
    upload = api.post(f"/projects/{PROJECT['id']}/files").mock(return_value=httpx.Response(201, json=FILE))
    create_run = api.post(f"/projects/{PROJECT['id']}/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    api.get(f"/runs/{RUN_QUEUED['id']}/events/stream").mock(return_value=sse_response(sse_text(EVENTS)))
    api.get(f"/runs/{RUN_QUEUED['id']}").mock(return_value=httpx.Response(200, json=RUN_SUCCEEDED))
    api.get(f"/runs/{RUN_QUEUED['id']}/artifacts").mock(
        return_value=httpx.Response(200, json=list_body([ARTIFACT_REPORT, ARTIFACT_MODEL]))
    )
    api.get(f"/artifacts/{ARTIFACT_REPORT['id']}/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": f"{MEDIA}/report",
                "expires_at": "2026-11-06T10:25:00Z",
                "format": "md",
                "content_type": "text/markdown",
                "size": 1,
            },
        )
    )
    api.get(f"/artifacts/{ARTIFACT_MODEL['id']}/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": f"{MEDIA}/model",
                "expires_at": "2026-11-06T10:25:00Z",
                "format": "step",
                "content_type": "model/step",
                "size": 1,
            },
        )
    )
    api.get(f"{MEDIA}/report").mock(return_value=httpx.Response(200, content=b"# Report"))
    api.get(f"{MEDIA}/model").mock(return_value=httpx.Response(200, content=b"ISO-10303-21;"))

    code = main(
        [
            *AUTH,
            "solve",
            "Design a bracket for the attached plate.",
            "--file",
            str(plate),
            "--budget-cents",
            "3000",
            "--out",
            str(out),
            "--instructions",
            "Use S355.",
            "--units",
            "metric",
            "--language",
            "en",
            "--allow-web",
            "--name",
            "Motor bracket, line 3",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert json.loads(create_project.calls.last.request.content) == {"name": "Motor bracket, line 3"}
    assert upload.call_count == 1
    body = json.loads(create_run.calls.last.request.content)
    assert body == {
        "engine": "mekint",
        "problem": "Design a bracket for the attached plate.",
        "budget_cents": 3000,
        "files": [FILE["id"]],
        "instructions": "Use S355.",
        "units": "metric",
        "language": "en",
        "allow_web": True,
    }
    assert (out / "bracket-report.md").read_bytes() == b"# Report"
    assert (out / "bracket.step").read_bytes() == b"ISO-10303-21;"
    assert "Reading the plate and the load cases." in captured.out
    assert "[succeeded]" in captured.out
    assert "run run_01jc5000a1b2: succeeded, cost 1420 cents" in captured.out
    assert "first mode 112 Hz" in captured.out
    assert "next: Confirm the motor centre-of-mass height." in captured.out
    assert f"project {PROJECT['id']}" in captured.err


def test_solve_on_an_existing_project_skips_creation(api: respx.MockRouter, tmp_path: Path) -> None:
    create_project = api.post("/projects")
    api.post("/projects/prj_9/runs").mock(return_value=httpx.Response(202, json=RUN_QUEUED))
    api.get(f"/runs/{RUN_QUEUED['id']}/events/stream").mock(return_value=sse_response(sse_text(EVENTS)))
    api.get(f"/runs/{RUN_QUEUED['id']}").mock(return_value=httpx.Response(200, json=RUN_SUCCEEDED))
    api.get(f"/runs/{RUN_QUEUED['id']}/artifacts").mock(return_value=httpx.Response(200, json=list_body([])))
    assert main([*AUTH, "solve", "x", "--project", "prj_9", "--out", str(tmp_path)]) == 0
    assert create_project.call_count == 0


def test_runs_projects_usage_limits_print_json(api: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(200, json=RUN_SUCCEEDED))
    api.get("/projects/prj_1/runs").mock(return_value=httpx.Response(200, json=list_body([RUN_SUCCEEDED])))
    api.post("/runs/run_1/cancel").mock(
        return_value=httpx.Response(200, json={**RUN_SUCCEEDED, "cancel_requested": True})
    )
    api.get("/projects").mock(return_value=httpx.Response(200, json=list_body([PROJECT])))
    api.get("/projects/prj_1").mock(return_value=httpx.Response(200, json=PROJECT))
    api.delete("/projects/prj_1").mock(return_value=httpx.Response(204))
    api.get("/limits").mock(return_value=httpx.Response(200, json=LIMITS))
    api.get("/usage").mock(return_value=httpx.Response(200, json={**USAGE, "period": "day"}))

    assert main([*AUTH, "runs", "get", "run_1"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "run_01jc5000a1b2"
    assert main([*AUTH, "runs", "list", "--project", "prj_1"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "succeeded"
    assert main([*AUTH, "runs", "cancel", "run_1"]) == 0
    assert json.loads(capsys.readouterr().out)["cancel_requested"] is True
    assert main([*AUTH, "projects", "list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == PROJECT["id"]
    assert main([*AUTH, "projects", "get", "prj_1"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "Motor bracket, line 3"
    assert main([*AUTH, "projects", "delete", "prj_1"]) == 0
    assert "deleted prj_1" in capsys.readouterr().out
    assert main([*AUTH, "limits"]) == 0
    assert json.loads(capsys.readouterr().out)["projects_per_account"] == 100
    assert main([*AUTH, "usage", "--period", "day"]) == 0
    assert json.loads(capsys.readouterr().out)["from"] == "2026-11-01T00:00:00Z"


def test_artifacts_download(api: respx.MockRouter, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api.get("/artifacts/art_01jc5201").mock(return_value=httpx.Response(200, json=ARTIFACT_REPORT))
    api.get("/artifacts/art_01jc5201/download").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": f"{MEDIA}/pdf",
                "expires_at": "2026-11-06T10:25:00Z",
                "format": "pdf",
                "content_type": "application/pdf",
                "size": 4,
            },
        )
    )
    api.get(f"{MEDIA}/pdf").mock(return_value=httpx.Response(200, content=b"%PDF"))
    assert main([*AUTH, "artifacts", "download", "art_01jc5201", "--format", "pdf", "--out", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "bracket-report.pdf")


def test_api_errors_exit_1(api: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    api.get("/runs/run_1").mock(
        return_value=httpx.Response(404, json=error_body("not_found_error", "not_found", "No such run."))
    )
    assert main([*AUTH, "runs", "get", "run_1"]) == 1
    assert "404 not_found: No such run." in capsys.readouterr().err


def test_connection_errors_exit_1(
    api: respx.MockRouter, capsys: pytest.CaptureFixture[str], sleeps: list[float]
) -> None:
    api.get("/limits").mock(side_effect=httpx.ConnectError("refused"))
    assert main([*AUTH, "limits"]) == 1
    assert "refused" in capsys.readouterr().err


def test_an_unreadable_answer_exits_1(api: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    api.get("/runs/run_1").mock(return_value=httpx.Response(200, json={"id": "run_1", "object": "run"}))
    assert main([*AUTH, "runs", "get", "run_1"]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_a_missing_file_exits_2(api: respx.MockRouter, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    create_project = api.post("/projects").mock(return_value=httpx.Response(201, json=PROJECT))
    assert main([*AUTH, "solve", "x", "--file", str(tmp_path / "missing.step"), "--out", str(tmp_path)]) == 2
    assert "No such file" in capsys.readouterr().err
    assert create_project.call_count == 1


def test_a_bad_idempotency_key_is_a_usage_error(api: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    from mechanicalai import Client

    with Client(api_key=API_KEY, base_url=BASE_URL) as client, pytest.raises(ValueError):
        client.projects.create(idempotency_key="bad key")
