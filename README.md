# mechanicalai

The official Python library for the [Mechanical AI API](https://mechanicalai.com): the same engine as
[mekint.com](https://mekint.com), driven from your code with an API key. A project holds your files; a run is a
message to the engine: it asks when it needs something and ends with its summary and the models, drawings, tables
and documents it made, with progress while it works.

```bash
pip install mechanicalai
```

Python 3.10 to 3.13. Sync and async clients, typed models, a `mechanicalai` command.

> The API is in preview. This library follows the published contract (`spec/public-api.openapi.yaml`); the version is
> `0.0.2` and the surface may still move before 1.0.

## Quickstart

```python
import mechanicalai
client = mechanicalai.Client()                      # MECHANICALAI_API_KEY
project = client.projects.create(name="Motor bracket, line 3")
plate = client.files.upload(project.id, "mounting-plate.step")

def follow(run):                                    # print progress until the run ends
    for event in client.runs.stream(run.id):
        print(event.at, event.text or event.status)
    return client.runs.retrieve(run.id)

run = follow(client.runs.create(project.id, engine="mekint", files=[plate.id],
    problem="Cast aluminium bracket for a 12 kg motor "
            "on four M8 holes. First mode above 500 Hz, "
            "3x the motor weight in every axis at a "
            "safety factor of 2, under 0.8 kg."))
while run.status == "needs_input":                  # the engine asked, as it does in the app: answer it
    run = follow(client.runs.reply(run.id, text=input(f"{run.question} ")))
print(run.status, run.cost_cents, run.summary, run.next_actions)
print(run.message)                                  # the engine's full final reply
for artifact in client.artifacts.list_for_run(run.id):
    client.artifacts.download(artifact.id, "./out")
```

A project is the durable container; a run is one job on it. A later run on the same project continues with the
engine's memory of it, so the next problem starts where the last one ended. A run is charged as it works, like the
app, from the account's API wallet, in integer US cents (`cost_cents`); when the wallet reaches zero, the run ends
`failed` with `failure.code == "insufficient_credit"`.

## Authentication

A key is `mk_` followed by 40 URL-safe characters, shown once when it is made. The client reads it from
`MECHANICALAI_API_KEY`, or takes it directly:

```python
client = mechanicalai.Client(api_key="mk_0000000000000000000000000000000000000000")
```

The key is sent only as `Authorization: Bearer` to the API host. It never goes to the signed upload and download URLs
on other hosts, is never written to disk and is redacted from debug logs.

`MECHANICALAI_BASE_URL` (or `base_url=`) points the client at another host; the default is
`https://api.mechanicalai.com/v1`.

## The client

```python
client = mechanicalai.Client(
    api_key=None,            # default: MECHANICALAI_API_KEY
    base_url=None,           # default: MECHANICALAI_BASE_URL or https://api.mechanicalai.com/v1
    timeout=None,            # default httpx.Timeout(60.0, connect=5.0); a float or an httpx.Timeout
    max_retries=2,           # retries after the first attempt
    default_headers=None,    # sent with every request
)
other = client.with_options(timeout=120, max_retries=5)   # a copy sharing the connection pools
with mechanicalai.Client() as client: ...                  # closes the pools on exit
```

All arguments are keyword-only. Resources, one attribute each: `client.engines`, `client.projects`,
`client.files`, `client.runs` (and `client.runs.events`), `client.artifacts`, `client.usage`. Every method returns a
typed pydantic model (or a page of them); unknown fields the API adds later are kept, never an error, and so is an
enum value the library does not know yet (a new run status or event kind parses as a plain string).
`model.request_id` is the `X-Request-ID` of the answer; quote it in support.

A `None` argument means "not given" and is left out of the request, everywhere except `projects.update`, where
`mechanicalai.NOT_GIVEN` is the sentinel and `name=None` sends `null` to clear the name.

## Async

`AsyncClient` has the same surface, awaited:

```python
import asyncio, mechanicalai

async def main() -> None:
    async with mechanicalai.AsyncClient() as client:
        run = await client.runs.create_in_new_project(engine="mekint", problem="A 2 kg gripper with 300 mm reach.")
        async for event in client.runs.stream(run.id):
            print(event.kind, event.text or event.status)
        run = await client.runs.wait(run.id)
        print(run.summary)

asyncio.run(main())
```

## Runs

```python
run = client.runs.create(project.id, engine="mekint", problem="...", files=[plate.id],
                         units="metric", language="en",
                         end_user="plant-3", metadata={"ticket": "ENG-4471"},
                         webhook_url="https://example.com/hooks/mechanicalai")
run = client.runs.create_in_new_project(engine="mekint", problem="...")   # names the project itself
run = client.runs.retrieve(run.id)
run = client.runs.wait(run.id, poll_interval=2.0, timeout=3600)   # until terminal; WaitTimeoutError after timeout
run = client.runs.cancel(run.id)                                  # stops at once; charged for what ran
page = client.runs.list(project.id)
```

`engine` is `"mekint"`; the run's `engine_version` says which snapshot ran. A run ends in one of four states:
`succeeded` (`run.message` is the engine's full final reply, `run.summary` its gist, `run.next_actions` what it
suggests next), `needs_input` (the engine asked one question, in `run.question`), `failed` (`run.failure.code`;
`insufficient_credit` when the wallet reached zero, `key_cap_reached` at the key's monthly cap) or `cancelled`.
Answer a question with a reply, which starts a new run on the same project with the engine's memory of the first,
as answering in the app does:

```python
if run.status == "needs_input":
    follow_up = client.runs.reply(run.id, text="Steel, 10 mm.")
```

One run at a time per project: a second one raises `ProjectBusyError` (409 `project_busy`).

## Streaming events

`runs.stream` yields the run's events as they happen (server-sent events) and stops after the terminal `status`:

```python
for event in client.runs.stream(run.id):
    if event.kind == "progress":
        print(event.text)
    elif event.kind == "task":
        print(event.text, event.percent, "%")
    elif event.kind == "status":
        print("->", event.status)
```

Kinds: `phase` (`thinking`, `working`), `progress`, `task` (with `percent` and `eta_s`), `note`, `status`; a kind
the library does not know is passed through. If the connection drops, the stream reconnects with `Last-Event-ID`
(exactly the last `id` received) and continues without a gap; the wait is the stream's own `retry:` hint when it
sent one, else 1 s doubling to 8 s, and it gives up after `max_reconnects` (5) drops in a row without an event.
Opened on a run that already ended, it replays every event. The same events are also a paged list:
`client.runs.events.list(run.id)`.

## Uploading files

```python
f = client.files.upload(project.id, "mounting-plate.step")                 # a path
f = client.files.upload(project.id, open("loads.xlsx", "rb"))               # an open binary file
f = client.files.upload(project.id, data, name="notes.txt")                 # bytes with a name
f = client.files.upload(project.id, ("plate.step", data), content_type="model/step")
```

Up to 64 MB a file goes in one multipart request; above that (or when the API answers 413) the library asks for a
signed upload URL, PUTs the bytes there with the sha256 digest declared, so the API verifies them at `complete`, and
completes the file. A path or a file backed by a real file descriptor is hashed and streamed from disk, never held
whole in memory; an open file is read from its current position. The file comes back `ready` and can be attached
to a run by id. `files.list`, `files.retrieve`, `files.download(file_id, dest)` and `files.delete` are there too.

## Downloading artifacts

What a run produced, flat: documents, models, tables, data, images.

```python
for artifact in client.artifacts.list_for_run(run.id):
    print(artifact.kind, artifact.name, artifact.formats)
    client.artifacts.download(artifact, "./out")                     # ./out/<name>.<default_format>
client.artifacts.download(model.id, "./parts/bracket-v2.stl", format="stl")   # an exact path
link = client.artifacts.download_url(model.id, format="glb")     # a signed link, valid 10 minutes
```

`download` fetches a fresh link and streams the bytes to disk; it returns the path written. A directory `dest`
(existing, ending with a separator, or without a file extension) takes `<name>.<format>`, with the name made safe
for any file system (a bare name, no control or reserved characters, no reserved Windows stems); anything else is
the file path. The bytes land in a temporary file beside the target and are moved into place once complete, so a
half-written file is never left under the final name. A model or table format is composed on first request: the API answers `202 preparing` and the library waits
and retries (up to `timeout`, 5 minutes by default). `artifacts.list_for_project(project.id, kind="model")` lists
across runs; `artifacts.retrieve` and `artifacts.delete` complete the set.

## Errors and retries

Every error the library raises is a `MechanicalAIError`:

- `APIConnectionError` (and `APITimeoutError`): the request got no answer.
- `APIStatusError`: an error answer, with `status_code`, `code`, `type`, `param`, `message`, `request_id`, `body`
  and, on limit refusals, `limit` and `count`. Its subclass follows the status: `BadRequestError` (400),
  `AuthenticationError` (401), `PaymentRequiredError` (402), `PermissionDeniedError` (403), `NotFoundError` (404),
  `ConflictError` (409), `GoneError` (410), `UnprocessableEntityError` (422), `RateLimitError` (429, with
  `retry_after`), `InternalServerError` (5xx); any other status is a plain `APIStatusError`. Five are chosen by
  `code`: `InsufficientCreditsError` (402 `insufficient_credit`), `ProjectBusyError` (409 `project_busy`),
  `IdempotencyAnswerLostError` and `IdempotencySupersededError` (409, see below), and `EngineRetiredError`
  (410 `engine_retired`, reserved for the day snapshots can be pinned; the API sends no 403, 410 or 422 today).
- `WaitTimeoutError`, `WebhookSignatureError`.

Handle by `code`; messages are for people and not stable.

```python
try:
    run = client.runs.create(project.id, engine="mekint", problem="...")
except mechanicalai.InsufficientCreditsError as e:
    print("add credit to the API wallet:", e.message)
except mechanicalai.ProjectBusyError:
    print("wait for the live run, or cancel it")
except mechanicalai.APIStatusError as e:
    print(e.status_code, e.code, e.request_id)
```

Connection errors, timeouts, 429 and 5xx answers are retried with exponential backoff and jitter (0.5 s doubling
to 8 s), honouring `Retry-After` up to 60 s, up to `max_retries` (2) times. A 429 on a create or a reply is the
app's message burst (`limits.messages_per_minute`, app messages and API runs together: "Too many requests at
once. Please wait a moment.", `Retry-After: 60`), and the client retries it like any other 429. A `POST` that carries no
`Idempotency-Key` (`runs.cancel`, `files.complete`) is the exception: it is never sent again after a connection
error, a timeout or a 5xx, because the server may already have acted on it; only its 429 is retried. Nothing else
is retried, so a 409 `project_busy` or a 400 surfaces at once. The same policy covers the signed upload and
download URLs: a failed PUT is resent whole from its source, a failed GET asked for again.

Every create (`projects.create`, `files.upload`, `runs.create`, `runs.create_in_new_project`, `runs.reply`,
`files.create_upload_url`) carries an `Idempotency-Key`: yours (`idempotency_key="order-4471"`, 1 to 64
characters of `[A-Za-z0-9._:-]`) or one the library generates, kept across retries, so a retried create never
makes a second object. The key is scoped to your account for 24 hours and fingerprinted with the request:

- the same key and request again replays the first answer (`Idempotent-Replayed: true`);
- a 409 `idempotency_in_progress` (the first is still being answered) is sent again by the library after its
  `Retry-After` (2 s), same key, same request, for up to 30 s regardless of `max_retries`; if it is still in
  progress after that, `ConflictError` is raised, and it is safe to send the same request with the same key again
  later;
- a 409 `idempotency_answer_lost` or `idempotency_superseded` means the create happened but its answer is not
  coming back on this key: the library raises `IdempotencyAnswerLostError` / `IdempotencySupersededError`
  (both `ConflictError`) and never resends, with this key or a new one, because that would make and charge a second
  run. Read the resource instead (`client.runs.list(project_id)`);
- a 409 `idempotency_conflict` is a different request under a used key: `ConflictError`.

Only 2xx, 400, 403, 404 and 422 answers are kept for replay. A 401, 402, 409, 413, 429 or 5xx is not, so the
same key sent again once the cause is fixed (a top-up after a 402 `insufficient_credit`) runs the request anew.

## Timeouts

`timeout` is the httpx timeout for every call: by default 60 s to read, write and acquire a connection and 5 s
to connect (`httpx.Timeout(60.0, connect=5.0)`); a float sets all four. Event streams carry a keep-alive every
15 s, so the read timeout only trips when the connection is really gone, and the stream reconnects. `runs.wait(timeout=...)` and `artifacts.download(timeout=...)` are separate, longer limits on
how long to keep polling.

## Pagination

Every list is a page with `data`, `has_more` and `next_cursor`; iterating the page walks every following page too:

```python
page = client.projects.list(limit=50)
page.data                                   # this page only
for project in page: ...                    # every project, fetching pages as needed
page.next_page()                            # or step by hand
page = client.projects.list(after=page.next_cursor)
```

With `AsyncClient`, `async for item in page` does the same (an `AsyncPage` has no sync iteration; `page.data` is
this page).

## Webhooks

A run with `webhook_url` posts its terminal event (`run.succeeded`, `run.needs_input`, `run.failed`,
`run.cancelled`) to that `https` URL, signed with the key's webhook secret from the console. Verify
the delivery with the raw body and the headers:

```python
from mechanicalai import webhooks, WebhookSignatureError

try:
    event = webhooks.verify(request.body, request.headers, secret=WEBHOOK_SECRET)   # body: the raw bytes
except WebhookSignatureError:
    return 400
run = event.data.run          # a full Run
event.type                    # "run.succeeded"
event.key_id                  # which key's secret signed it (Mechanicalai-Key-Id)
```

The check is a constant-time HMAC-SHA256 of `"<t>.<body>"` from `Mechanicalai-Signature: t=...,v1=...`, and a
signature older than `tolerance_s` (300) is refused; `key_id` is read only after the signature checked out.
A delivery that gets no 2xx is retried after 5 s, 1, 5, 15 and 60 minutes (six attempts in all); a receiver must
tolerate a duplicate `Mechanicalai-Event-Id`.

## Usage and limits

```python
usage = client.usage.retrieve(period="month", group_by="end_user")
usage.runs, usage.cost_cents, usage.buckets, usage.by_end_user["plant-3"].cost_cents
limits = client.usage.limits()
limits.projects_per_user, limits.messages_per_minute, limits.user_char_limit, limits.usage.tool_runs
```

The limits are the app's own numbers under the names its rules give them, plus the three that exist for the API
alone (`files_per_project`, `storage_bytes`, `requests_per_minute`).

```python
limits.reference_file_upload_bytes                 # the multipart upload's bound
```

## CLI

The package installs a `mechanicalai` command (`MECHANICALAI_API_KEY` in the environment, or `--api-key`):

```bash
mechanicalai solve "Design a bracket for the attached plate." --file plate.step --out ./out
mechanicalai solve "Re-check it at 5x load." --project prj_... --language ko
mechanicalai runs reply run_... "Steel, 10 mm." --file loads.xlsx --out ./out
mechanicalai runs get run_...
mechanicalai runs list --project prj_...
mechanicalai runs cancel run_...
mechanicalai artifacts download art_... --format pdf --out ./out
mechanicalai projects list | get prj_... | delete prj_...
mechanicalai usage --period day
mechanicalai limits
mechanicalai --version
```

`solve` creates a project (or uses `--project`), uploads the files, starts the run, prints events as they stream,
downloads every artifact into `--out` and prints the engine's reply. When the run ends `needs_input`, it prints
the question; `runs reply` answers it (with `--file` attachments) and follows the new run the same way. Exit
codes: 0 on success, 1 on an API or connection error, 2 on a usage error.

## Logging

`MECHANICALAI_LOG=info` logs every request and answer (method, URL, status, request id) to the `mechanicalai`
logger; `debug` adds headers and bodies, with the key and the query strings of signed URLs redacted.

## Versioning

Semantic versioning. Before 1.0, a minor version may change the surface; a deprecation is announced one minor
version ahead of its removal, in `CHANGELOG.md` and with a `DeprecationWarning`. The library is built against the
API contract in `spec/`; new fields in the API never break an older library, because every model keeps unknown
fields.

Supported Python: 3.10, 3.11, 3.12, 3.13.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports go to security@mechanicalai.com ([SECURITY.md](SECURITY.md)).

## License

Apache-2.0. See [LICENSE](LICENSE).
