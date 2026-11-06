# Contributing

Thanks for helping with the library. This page is the whole setup.

## Setup

[uv](https://docs.astral.sh/uv/) manages the environment:

```bash
git clone https://github.com/mechanical-ai/mechanicalai-python
cd mechanicalai-python
uv sync --all-groups
uvx pre-commit install      # optional: ruff and the hygiene hooks on every commit
```

## Checks

Everything CI runs, in the order it runs it:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
```

`uv run ruff format .` and `uv run ruff check --fix .` fix most findings.

The tests mock HTTP with [respx](https://lundberg.github.io/respx/); none of them calls the real API.
`tests/test_mock_server.py` runs the README quickstart against a [Prism](https://stoplight.io/open-source/prism) mock of
`spec/public-api.openapi.yaml`; it needs `npx` on the PATH and skips itself otherwise, or when
`MECHANICALAI_SKIP_PRISM` is set (CI runs it on one Python version only).

## Layout

- `src/mechanicalai/_client.py`: `Client`, `AsyncClient`
- `src/mechanicalai/_base_client.py`: the httpx transport, retries, idempotency keys, error mapping
- `src/mechanicalai/resources/`: one module per API resource, a sync and an async class each
- `src/mechanicalai/types/`: pydantic models for the API's objects, TypedDicts for request bodies
- `src/mechanicalai/_streaming.py`: server-sent events; `_files.py`: uploads; `webhooks.py`: signature checks
- `src/mechanicalai/_cli.py`: the `mechanicalai` command
- `spec/public-api.openapi.yaml`: the API contract the library follows

Models are plain data; behaviour lives on the resources. Every model keeps unknown fields, so a new field in the API
never breaks an older library.

## Commits and pull requests

Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(runs): ...`, `fix(streaming): ...`,
`docs: ...`, `test: ...`, `chore: ...`. A pull request is one change, with tests, and a line under *Unreleased* in
`CHANGELOG.md` when the change is visible to users.

This repository is public. Nothing internal, no secrets, no real keys: example keys look like
`mk_test_000000000000000000000000000000000000test`, example addresses like `test@company.com`.

## Releasing

Maintainers: bump `version` in `pyproject.toml` and `src/mechanicalai/_version.py`, move the *Unreleased* entries under
the new version in `CHANGELOG.md`, tag `vX.Y.Z` and publish a GitHub release. The `publish` workflow runs the tests,
builds and uploads to PyPI through trusted publishing.
