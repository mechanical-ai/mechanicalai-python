"""Verify a webhook delivery.

A run with ``webhook_url`` posts its terminal event there with ``Mechanicalai-Signature: t=<unix seconds>,v1=<hex>``,
where ``v1`` is the HMAC-SHA256 of ``"<t>.<body>"`` under the key's webhook secret (set in the console), and
``Mechanicalai-Key-Id`` naming the key whose secret signed it. ``verify`` checks the signature in constant time,
refuses one older than ``tolerance_s``, and returns the parsed event::

    event = mechanicalai.webhooks.verify(request.body, request.headers, secret)
    run = event.data.run
    event.key_id          # the signing key's id, from Mechanicalai-Key-Id, read only once the signature held

The body must be the raw bytes as received: a decoded ``str`` may not re-encode to what was signed, so it is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

from mechanicalai._exceptions import WebhookSignatureError
from mechanicalai.types.webhook_event import WebhookEvent

__all__ = ["EVENT_ID_HEADER", "KEY_ID_HEADER", "SIGNATURE_HEADER", "sign", "verify"]

SIGNATURE_HEADER = "Mechanicalai-Signature"
EVENT_ID_HEADER = "Mechanicalai-Event-Id"
KEY_ID_HEADER = "Mechanicalai-Key-Id"
DEFAULT_TOLERANCE_S = 300


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _parse_signature(value: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in value.split(","):
        key, sep, item = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                timestamp = int(item)
            except ValueError as exc:
                raise WebhookSignatureError("Malformed signature header: t is not an integer.") from exc
        elif key == "v1":
            signatures.append(item.strip().lower())
    if timestamp is None:
        raise WebhookSignatureError("Malformed signature header: no t=.")
    if not signatures:
        raise WebhookSignatureError("Malformed signature header: no v1=.")
    return timestamp, signatures


def _digest(secret: str, timestamp: int, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()


def sign(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """The ``Mechanicalai-Signature`` value for a body (to build deliveries in tests)."""
    t = int(time.time()) if timestamp is None else timestamp
    return f"t={t},v1={_digest(secret, t, payload)}"


def verify(
    payload: bytes,
    headers: Mapping[str, str],
    secret: str,
    *,
    tolerance_s: int | None = DEFAULT_TOLERANCE_S,
) -> WebhookEvent:
    """Check a delivery's signature and age, and return its event. Raises ``WebhookSignatureError`` otherwise.

    ``tolerance_s`` is how old (or how far in the future) the signature's timestamp may be; ``None`` skips the
    age check.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload is the raw body as bytes, exactly as received; a decoded str cannot be verified.")
    if not secret:
        raise ValueError("A webhook secret is needed to verify a delivery.")
    header = _header(headers, SIGNATURE_HEADER)
    if not header:
        raise WebhookSignatureError(f"No {SIGNATURE_HEADER} header.")
    timestamp, signatures = _parse_signature(header)
    expected = _digest(secret, timestamp, bytes(payload))
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise WebhookSignatureError("The signature does not match.")
    age = time.time() - timestamp
    if tolerance_s is not None and abs(age) > tolerance_s:
        raise WebhookSignatureError(f"The signature is {int(age)} s old; the tolerance is {tolerance_s} s.")
    try:
        event = WebhookEvent.model_validate_json(bytes(payload))
    except ValueError as exc:
        raise WebhookSignatureError(f"The body is not a webhook event: {exc}") from exc
    event._key_id = _header(headers, KEY_ID_HEADER)
    return event
