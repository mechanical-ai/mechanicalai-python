# Changelog

All notable changes to this project are documented here. The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.4] - 2026-11-07

The API is the app driven by a script, and the library follows: what the API had on top of the app is gone.

### Removed

- `budget_cents` on `runs.create`, `runs.create_in_new_project` and `runs.reply`, on `Run`, `RunCreateParams` and
  `RunReplyParams`, and the CLI's `--budget-cents`. A run is charged as it works; one that runs out of credit ends
  `failed` with `failure.code == "insufficient_credit"`. The `budget_exhausted` status is gone from `RunStatus`,
  `TERMINAL_RUN_STATUSES` and `WebhookEventType` (`run.budget_exhausted`).
- The `instructions` and `allow_web` run options, on the resources, `Run`, `RunCreateParams` and the CLI
  (`--instructions`, `--allow-web`).
- `Run.report_artifact_id` and the `report` artifact kind: a run ends with `summary`, `next_actions` and its
  artifacts (documents, models, tables, data, images).
- Test keys and the `mk_live_` prefix: a key is `mk_` followed by 40 URL-safe characters.
- `Limits.projects_per_account`, `files_per_run`, `concurrent_runs_per_key`, `upload_bytes_max`,
  `problem_chars_max`, `instructions_chars_max` and `LimitsUsage.running_runs_this_key`. `Limits` now carries the
  app's own names: `projects_per_user`, `files_per_project`, `storage_bytes`, `concurrent_tool_runs`,
  `messages_per_minute`, `requests_per_minute`, `reference_file_upload_bytes`, `user_char_limit` and
  `usage.{projects, storage_bytes, tool_runs, running_runs}`.

### Added

- `Run.message`: the engine's full final reply, null until the run ends.
- `insufficient_credit` and `key_cap_reached` in `RunFailureCode`.
- `mechanicalai runs reply <run_id> "<text>" [--file F] [--out DIR]`: answers a `needs_input` run and follows the
  new run as `solve` does; `solve` names the command when a run ends with a question.

### Changed

- The README quickstart answers a `needs_input` run with `runs.reply`, as the app's user answers a question, and poses a cast aluminium bracket.

### Fixed

- The package version is read from `_version.py` at build time; `pyproject.toml` no longer carries its own copy. The `v0.0.3` tag was never published: its build carried the stale `0.0.2` and PyPI refused it.
- A 429 on a create or a reply is the account's message burst (`Limits.messages_per_minute`, `Retry-After` the
  window); the client retries it as it always did any 429.

## [0.0.2] - 2026-11-06

The first version of the library, built against the API contract (`spec/public-api.openapi.yaml`).

### Added

- `Client` and `AsyncClient` with `api_key`, `base_url`, `timeout`, `max_retries`, `default_headers`, `with_options`
  and context-manager support.
- Resources: `engines`, `projects`, `files`, `runs` (with `runs.events`), `artifacts`, `usage`.
- `runs.stream`: server-sent events with resume on a dropped connection (`Last-Event-ID`).
- `runs.wait`: poll until a run is terminal.
- `files.upload`: multipart up to 64 MB, a signed upload URL above that, with the sha256 digest declared.
- `artifacts.download` and `files.download`: fetch a fresh link and stream the bytes to disk.
- `artifacts.download_url` and `artifacts.download` wait while a format is composed (202 `preparing`).
- Retries with exponential backoff and jitter on connection errors, 429 and 5xx, honouring `Retry-After`; a `POST`
  without an `Idempotency-Key` is never resent after a failure that may have reached the server; a 409
  `idempotency_in_progress` is sent again as is after its `Retry-After` for up to 30 s.
- An `Idempotency-Key` (1 to 64 characters of `[A-Za-z0-9._:-]`) on every create when the caller gives none.
- A typed exception hierarchy, with `InsufficientCreditsError`, `ProjectBusyError`, `IdempotencyAnswerLostError` and
  `IdempotencySupersededError` chosen by the error code.
- `webhooks.verify` for signed deliveries, exposing the signing key's id as `event.key_id`.
- The `mechanicalai` command: `solve`, `runs`, `artifacts`, `projects`, `usage`, `limits`.

[Unreleased]: https://github.com/mechanical-ai/mechanicalai-python/compare/v0.0.2...HEAD
[0.0.2]: https://github.com/mechanical-ai/mechanicalai-python/releases/tag/v0.0.2
