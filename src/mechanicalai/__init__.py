"""The official Python library for the Mechanical AI API.

It drives the same engine as mekint.com from your code with an API key. A project holds your files; a run is a
message to the engine: it asks when it needs something and ends with its summary and the models, drawings, tables
and documents it made.

::

    import mechanicalai

    client = mechanicalai.Client()                       # reads MECHANICALAI_API_KEY
    project = client.projects.create(name="Motor bracket, line 3")
    plate = client.files.upload(project.id, "mounting-plate.step")

    def follow(run):                                     # print progress until the run ends
        for event in client.runs.stream(run.id):
            print(event.at, event.text or event.status)
        return client.runs.retrieve(run.id)

    run = follow(client.runs.create(project.id, engine="mekint", files=[plate.id],
                                    problem="Design a steel bracket that mounts the 12 kg motor on the plate."))
    while run.status == "needs_input":                   # the engine asked, as it does in the app: answer it
        run = follow(client.runs.reply(run.id, text=input(f"{run.question} ")))
    print(run.status, run.cost_cents, run.summary)
    print(run.message)                                   # the engine's full final reply
    for artifact in client.artifacts.list_for_run(run.id):
        client.artifacts.download(artifact.id, "./out")

Arguments: ``None`` means "not given" and is left out of the request, everywhere except ``projects.update``, where
``NOT_GIVEN`` is the sentinel and ``name=None`` sends ``null`` to clear the name.
"""

from mechanicalai import types, webhooks
from mechanicalai._base_client import DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT
from mechanicalai._client import AsyncClient, Client
from mechanicalai._exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    EngineRetiredError,
    GoneError,
    IdempotencyAnswerLostError,
    IdempotencySupersededError,
    InsufficientCreditsError,
    InternalServerError,
    MechanicalAIError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    ProjectBusyError,
    RateLimitError,
    UnprocessableEntityError,
    WaitTimeoutError,
    WebhookSignatureError,
)
from mechanicalai._files import FileInput
from mechanicalai._streaming import AsyncRunEventStream, RunEventStream
from mechanicalai._version import __version__
from mechanicalai.pagination import AsyncPage, SyncPage
from mechanicalai.resources._base import NOT_GIVEN, NotGiven

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "NOT_GIVEN",
    "APIConnectionError",
    "APIStatusError",
    "APITimeoutError",
    "AsyncClient",
    "AsyncPage",
    "AsyncRunEventStream",
    "AuthenticationError",
    "BadRequestError",
    "Client",
    "ConflictError",
    "EngineRetiredError",
    "FileInput",
    "GoneError",
    "IdempotencyAnswerLostError",
    "IdempotencySupersededError",
    "InsufficientCreditsError",
    "InternalServerError",
    "MechanicalAIError",
    "NotFoundError",
    "NotGiven",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "ProjectBusyError",
    "RateLimitError",
    "RunEventStream",
    "SyncPage",
    "UnprocessableEntityError",
    "WaitTimeoutError",
    "WebhookSignatureError",
    "__version__",
    "types",
    "webhooks",
]
