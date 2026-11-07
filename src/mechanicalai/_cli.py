"""The ``mechanicalai`` command.

::

    mechanicalai solve "Design a bracket for the attached plate." --file plate.step --out ./out
    mechanicalai runs reply run_... "Steel, 10 mm." --file loads.xlsx --out ./out
    mechanicalai runs get run_...
    mechanicalai artifacts download art_... --format pdf --out ./out
    mechanicalai projects list
    mechanicalai usage
    mechanicalai limits
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mechanicalai._client import Client
from mechanicalai._exceptions import APIConnectionError, MechanicalAIError
from mechanicalai._version import __version__
from mechanicalai.types._base import BaseModel
from mechanicalai.types.run import Run

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mechanicalai", description="The Mechanical AI API from the command line.")
    parser.add_argument("--version", action="version", version=f"mechanicalai {__version__}")
    parser.add_argument("--api-key", help="The API key (default: MECHANICALAI_API_KEY).")
    parser.add_argument("--base-url", help="The API base URL (default: MECHANICALAI_BASE_URL or the production API).")
    commands = parser.add_subparsers(dest="command", metavar="command")

    solve = commands.add_parser("solve", help="Start a run, follow its progress and download what it produced.")
    solve.add_argument("problem", help="The problem, in plain words.")
    solve.add_argument("--file", action="append", default=[], metavar="F", help="A file to upload (repeatable).")
    solve.add_argument("--project", metavar="ID", help="An existing project (default: a new one).")
    solve.add_argument("--engine", default="mekint", help="The engine (default: mekint).")
    solve.add_argument("--out", default="./out", metavar="DIR", help="Where artifacts are written (default ./out).")
    solve.add_argument("--units", choices=["metric", "imperial"])
    solve.add_argument("--language", choices=["en", "ko"])
    solve.add_argument("--name", help="The new project's name (ignored with --project).")
    solve.set_defaults(func=cmd_solve)

    runs = commands.add_parser("runs", help="Runs.")
    runs_sub = runs.add_subparsers(dest="subcommand", metavar="action")
    runs_get = runs_sub.add_parser("get", help="Show a run.")
    runs_get.add_argument("run_id")
    runs_get.set_defaults(func=cmd_runs_get)
    runs_list = runs_sub.add_parser("list", help="List a project's runs.")
    runs_list.add_argument("--project", required=True, metavar="ID")
    runs_list.add_argument("--limit", type=int)
    runs_list.set_defaults(func=cmd_runs_list)
    runs_cancel = runs_sub.add_parser("cancel", help="Cancel a run.")
    runs_cancel.add_argument("run_id")
    runs_cancel.set_defaults(func=cmd_runs_cancel)
    runs_reply = runs_sub.add_parser("reply", help="Answer a run's question and follow the new run like solve.")
    runs_reply.add_argument("run_id", help="The run that ended needs_input.")
    runs_reply.add_argument("text", help="The answer, in plain words.")
    runs_reply.add_argument("--file", action="append", default=[], metavar="F", help="A file to attach (repeatable).")
    runs_reply.add_argument(
        "--out", default="./out", metavar="DIR", help="Where artifacts are written (default ./out)."
    )
    runs_reply.set_defaults(func=cmd_runs_reply)

    artifacts = commands.add_parser("artifacts", help="Artifacts.")
    artifacts_sub = artifacts.add_subparsers(dest="subcommand", metavar="action")
    art_download = artifacts_sub.add_parser("download", help="Download an artifact.")
    art_download.add_argument("artifact_id")
    art_download.add_argument("--format", help="One of the artifact's formats (default: its default format).")
    art_download.add_argument("--out", default=".", metavar="DIR", help="A directory or a file path (default .).")
    art_download.set_defaults(func=cmd_artifacts_download)

    projects = commands.add_parser("projects", help="Projects.")
    projects_sub = projects.add_subparsers(dest="subcommand", metavar="action")
    projects_list = projects_sub.add_parser("list", help="List projects.")
    projects_list.add_argument("--limit", type=int)
    projects_list.set_defaults(func=cmd_projects_list)
    projects_get = projects_sub.add_parser("get", help="Show a project.")
    projects_get.add_argument("project_id")
    projects_get.set_defaults(func=cmd_projects_get)
    projects_delete = projects_sub.add_parser("delete", help="Delete a project and everything in it.")
    projects_delete.add_argument("project_id")
    projects_delete.set_defaults(func=cmd_projects_delete)

    usage = commands.add_parser("usage", help="This month's usage.")
    usage.add_argument("--period", choices=["day", "month"])
    usage.add_argument("--group-by", choices=["end_user"])
    usage.set_defaults(func=cmd_usage)

    limits = commands.add_parser("limits", help="The account's limits and counts.")
    limits.set_defaults(func=cmd_limits)
    return parser


def _client(args: argparse.Namespace) -> Client:
    return Client(api_key=args.api_key, base_url=args.base_url)


def _print(model: BaseModel | list[BaseModel]) -> None:
    if isinstance(model, list):
        print(json.dumps([m.to_dict() for m in model], indent=2))
    else:
        print(json.dumps(model.to_dict(), indent=2))


def _print_summary(run: Run) -> None:
    print(f"run {run.id}: {run.status}, cost {run.cost_cents} cents")
    text = run.message or run.summary
    if text:
        print(text)
    if run.question:
        print(f"question: {run.question}")
        print(f'answer with: mechanicalai runs reply {run.id} "..."')
    if run.failure:
        print(f"failure: {run.failure.code}: {run.failure.message}")
    for action in run.next_actions:
        print(f"next: {action}")


def _upload(client: Client, project_id: str, paths: Sequence[str]) -> list[str]:
    file_ids: list[str] = []
    for path in paths:
        uploaded = client.files.upload(project_id, path)
        file_ids.append(uploaded.id)
        print(f"uploaded {uploaded.name} as {uploaded.id}", file=sys.stderr)
    return file_ids


def _follow(client: Client, run: Run, out: str) -> int:
    """Print the run's events as they stream, download what it produced into ``out`` and print the summary."""
    print(f"run {run.id}", file=sys.stderr)
    for event in client.runs.stream(run.id):
        stamp = event.at.strftime("%H:%M:%S")
        if event.kind == "status":
            print(f"{stamp}  [{event.status}]")
        elif event.kind == "phase":
            print(f"{stamp}  ({event.phase})")
        elif event.kind == "task" and event.percent is not None:
            print(f"{stamp}  {event.text} {event.percent}%")
        else:
            print(f"{stamp}  {event.text}")
    run = client.runs.retrieve(run.id)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for artifact in client.artifacts.list_for_run(run.id):
        target = client.artifacts.download(artifact, out_dir)
        print(f"wrote {target}", file=sys.stderr)
    _print_summary(run)
    return EXIT_OK


def cmd_solve(client: Client, args: argparse.Namespace) -> int:
    if args.project:
        project_id = args.project
    else:
        project = client.projects.create(name=args.name)
        project_id = project.id
        print(f"project {project_id}", file=sys.stderr)
    file_ids = _upload(client, project_id, args.file)
    run = client.runs.create(
        project_id,
        engine=args.engine,
        problem=args.problem,
        files=file_ids or None,
        units=args.units,
        language=args.language,
    )
    return _follow(client, run, args.out)


def cmd_runs_reply(client: Client, args: argparse.Namespace) -> int:
    file_ids = _upload(client, client.runs.retrieve(args.run_id).project_id, args.file) if args.file else []
    run = client.runs.reply(args.run_id, text=args.text, files=file_ids or None)
    return _follow(client, run, args.out)


def cmd_runs_get(client: Client, args: argparse.Namespace) -> int:
    _print(client.runs.retrieve(args.run_id))
    return EXIT_OK


def cmd_runs_list(client: Client, args: argparse.Namespace) -> int:
    _print(list(client.runs.list(args.project, limit=args.limit).data))
    return EXIT_OK


def cmd_runs_cancel(client: Client, args: argparse.Namespace) -> int:
    _print(client.runs.cancel(args.run_id))
    return EXIT_OK


def cmd_artifacts_download(client: Client, args: argparse.Namespace) -> int:
    target = client.artifacts.download(args.artifact_id, args.out, format=args.format)
    print(target)
    return EXIT_OK


def cmd_projects_list(client: Client, args: argparse.Namespace) -> int:
    _print(list(client.projects.list(limit=args.limit).data))
    return EXIT_OK


def cmd_projects_get(client: Client, args: argparse.Namespace) -> int:
    _print(client.projects.retrieve(args.project_id))
    return EXIT_OK


def cmd_projects_delete(client: Client, args: argparse.Namespace) -> int:
    client.projects.delete(args.project_id)
    print(f"deleted {args.project_id}")
    return EXIT_OK


def cmd_usage(client: Client, args: argparse.Namespace) -> int:
    _print(client.usage.retrieve(period=args.period, group_by=args.group_by))
    return EXIT_OK


def cmd_limits(client: Client, args: argparse.Namespace) -> int:
    _print(client.usage.limits())
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    func: Any = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        with _client(args) as client:
            result: int = func(client, args)
            return result
    except MechanicalAIError as exc:
        print(f"mechanicalai: {exc.message if isinstance(exc, APIConnectionError) else exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValidationError as exc:
        print(f"mechanicalai: the API answered with something this version cannot read: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError as exc:
        print(f"mechanicalai: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:
        print(f"mechanicalai: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"mechanicalai: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("mechanicalai: interrupted; a started run keeps going (cancel it with `runs cancel`)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
