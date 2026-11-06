from __future__ import annotations

import json
import time

import pytest

from mechanicalai import WebhookSignatureError, webhooks
from tests.conftest import RUN_SUCCEEDED

SECRET = "whsec_test_000000000000000000000000"


def _delivery() -> bytes:
    body = {
        "id": "wh_01jc5100",
        "object": "event",
        "type": "run.succeeded",
        "created_at": "2026-11-06T10:11:30Z",
        "data": {"run": RUN_SUCCEEDED},
    }
    return json.dumps(body).encode()


def test_a_good_signature_verifies() -> None:
    payload = _delivery()
    headers = {
        "Mechanicalai-Signature": webhooks.sign(payload, SECRET),
        "Mechanicalai-Event-Id": "wh_01jc5100",
        "Mechanicalai-Key-Id": "key_01jc4w00",
    }
    event = webhooks.verify(payload, headers, SECRET)
    assert event.id == "wh_01jc5100"
    assert event.type == "run.succeeded"
    assert event.key_id == "key_01jc4w00"
    assert event.data.run.id == RUN_SUCCEEDED["id"]
    assert event.data.run.cost_cents == 1420


def test_header_names_are_case_insensitive() -> None:
    payload = _delivery()
    event = webhooks.verify(payload, {"mechanicalai-signature": webhooks.sign(payload, SECRET)}, SECRET)
    assert event.id == "wh_01jc5100"
    assert event.key_id is None


def test_a_bad_signature_is_refused() -> None:
    payload = _delivery()
    headers = {"Mechanicalai-Signature": webhooks.sign(payload, "another-secret")}
    with pytest.raises(WebhookSignatureError, match="does not match"):
        webhooks.verify(payload, headers, SECRET)
    tampered = payload.replace(b"1420", b"1")
    with pytest.raises(WebhookSignatureError, match="does not match"):
        webhooks.verify(tampered, {"Mechanicalai-Signature": webhooks.sign(payload, SECRET)}, SECRET)


def test_a_stale_timestamp_is_refused_within_tolerance_only() -> None:
    payload = _delivery()
    old = int(time.time()) - 600
    headers = {"Mechanicalai-Signature": webhooks.sign(payload, SECRET, timestamp=old)}
    with pytest.raises(WebhookSignatureError, match="old"):
        webhooks.verify(payload, headers, SECRET)
    assert webhooks.verify(payload, headers, SECRET, tolerance_s=1000).type == "run.succeeded"


def test_missing_or_malformed_headers() -> None:
    payload = _delivery()
    with pytest.raises(WebhookSignatureError, match="No Mechanicalai-Signature"):
        webhooks.verify(payload, {}, SECRET)
    with pytest.raises(WebhookSignatureError, match="no v1"):
        webhooks.verify(payload, {"Mechanicalai-Signature": "t=1"}, SECRET)
    with pytest.raises(WebhookSignatureError, match="no t"):
        webhooks.verify(payload, {"Mechanicalai-Signature": "v1=abc"}, SECRET)
    with pytest.raises(WebhookSignatureError, match="integer"):
        webhooks.verify(payload, {"Mechanicalai-Signature": "t=now,v1=abc"}, SECRET)
    with pytest.raises(ValueError):
        webhooks.verify(payload, {"Mechanicalai-Signature": "t=1,v1=abc"}, "")


def test_several_v1_values_are_accepted_when_one_matches() -> None:
    payload = _delivery()
    good = webhooks.sign(payload, SECRET)
    t, v1 = good.split(",")
    headers = {"Mechanicalai-Signature": f"{t},v1=deadbeef,{v1}"}
    assert webhooks.verify(payload, headers, SECRET).type == "run.succeeded"


def test_a_signed_body_that_is_not_an_event_is_refused() -> None:
    payload = b'{"hello": "world"}'
    with pytest.raises(WebhookSignatureError, match="not a webhook event"):
        webhooks.verify(payload, {"Mechanicalai-Signature": webhooks.sign(payload, SECRET)}, SECRET)


def test_a_str_body_is_refused() -> None:
    payload = _delivery()
    with pytest.raises(TypeError, match="raw body as bytes"):
        webhooks.verify(payload.decode(), {"Mechanicalai-Signature": webhooks.sign(payload, SECRET)}, SECRET)  # type: ignore[arg-type]


def test_tolerance_none_skips_the_age_check() -> None:
    payload = _delivery()
    headers = {"Mechanicalai-Signature": webhooks.sign(payload, SECRET, timestamp=1)}
    with pytest.raises(WebhookSignatureError, match="old"):
        webhooks.verify(payload, headers, SECRET)
    assert webhooks.verify(payload, headers, SECRET, tolerance_s=None).type == "run.succeeded"


def test_unknown_event_types_parse() -> None:
    payload = _delivery().replace(b"run.succeeded", b"run.archived")
    event = webhooks.verify(payload, {"Mechanicalai-Signature": webhooks.sign(payload, SECRET)}, SECRET)
    assert event.type == "run.archived"
