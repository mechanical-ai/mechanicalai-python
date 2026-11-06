# Changelog

All notable changes to this project are documented here. The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
