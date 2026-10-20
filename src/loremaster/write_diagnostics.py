"""Bounded write receipts without upstream text, credentials or arbitrary keys."""

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any

import httpx

from loremaster.kanka import KankaError, _reject_nonfinite_json, _retry_delay


def response_receipt(response: httpx.Response) -> dict[str, Any]:
    """Record headers before reading a possibly interrupted response body."""
    media = response.headers.get("content-type", "").split(";", 1)[0].lower()
    return {
        "stage": "response_headers",
        "http_status": response.status_code,
        "media_type": media
        if media in ("application/json", "text/html", "text/plain")
        else "other",
    }


def json_receipt(response: httpx.Response) -> tuple[Any, dict[str, Any]]:
    """Describe JSON shapes and positive candidate IDs, never arbitrary values.

    A candidate ID is a recovery hint, not verified identity or successful canon.
    Call only after the body is completely read; malformed JSON remains uncertain.
    """
    receipt = {
        **response_receipt(response),
        "stage": "response_body",
        "response_bytes": len(response.content),
        "response_sha256": hashlib.sha256(response.content).hexdigest(),
    }
    try:
        payload = response.json(parse_constant=_reject_nonfinite_json)
    except ValueError:
        return None, {**receipt, "failure": "invalid_json"}
    if not isinstance(payload, dict) or "data" not in payload:
        return payload, {**receipt, "failure": "invalid_envelope"}
    data = payload["data"]
    receipt["data_shape"] = (
        "object"
        if isinstance(data, dict)
        else "array"
        if isinstance(data, list)
        else "scalar"
    )
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if isinstance(data, dict):
        receipt["identity_types"] = {
            key: "missing"
            if key not in data
            else "integer"
            if type(data[key]) is int
            else "boolean"
            if type(data[key]) is bool
            else "string"
            if isinstance(data[key], str)
            else "other"
            for key in (
                "id",
                "entity_id",
                "owner_id",
                "organisation_id",
                "character_id",
            )
        }
        receipt["candidate_ids"] = {
            key: data[key]
            for key in receipt["identity_types"]
            if type(data.get(key)) is int and 0 < data[key] <= 2**63 - 1
        }
    return payload, receipt


def transport_failure(error: httpx.RequestError) -> str:
    """Return a fixed category, never exception text containing an URL or token."""
    for kind, label in (
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.RemoteProtocolError, "remote_protocol_error"),
        (httpx.DecodingError, "body_decoding_error"),
    ):
        if isinstance(error, kind):
            return label
    return "transport_error"


async def send_write(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any],
    record: Callable[[dict[str, Any]], None],
    observe: Callable[[httpx.Response], None],
) -> tuple[httpx.Response, Any, dict[str, Any]]:
    """Persist headers before body I/O; never retry or copy upstream error text."""
    receipt: dict[str, Any] = {"stage": "request"}
    record(receipt)
    try:
        async with client.stream(method, path, json=body) as response:
            receipt = response_receipt(response)
            record(receipt)
            observe(response)
            if not 200 <= response.status_code < 300:
                receipt["failure"] = "http_error"
                if response.status_code == 429:
                    receipt["retry_after_seconds"] = _retry_delay(
                        response.headers.get("Retry-After", ""), 0
                    )
                record(receipt)
                return response, None, receipt
            receipt["stage"] = "response_body"
            record(receipt)
            await response.aread()
            payload, receipt = json_receipt(response)
            record(receipt)
            return response, payload, receipt
    except asyncio.CancelledError:
        record({**receipt, "failure": "cancelled"})
        raise
    except httpx.RequestError as error:
        failure = transport_failure(error)
        record({**receipt, "failure": failure})
        raise KankaError(
            f"Write response is uncertain ({failure}); inspect diagnostics",
            code="uncertain_result",
            status_code=receipt.get("http_status"),
        ) from None
