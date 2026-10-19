"""Bound slow operations and untrusted Retry-After headers without live HTTP."""

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from loremaster.kanka import KankaClient, _retry_delay
from loremaster.server import create_server


@pytest.mark.parametrize("seconds, expected", [(5, 5.0), (-5, 0.0), (60, 60.0)])
def test_retry_after_http_dates(seconds, expected, monkeypatch):
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    clock = Mock()
    clock.now.return_value = now
    monkeypatch.setattr("loremaster.kanka.datetime", clock)
    assert (
        _retry_delay(format_datetime(now + timedelta(seconds=seconds), usegmt=True), 0)
        == expected
    )


@pytest.mark.parametrize(
    "header, attempt, expected",
    [
        (" 3 ", 0, 3.0),
        ("", 0, 2.0),
        ("invalid", 1, 4.0),
        ("9" * 5000, 0, 31),
        ("-1", 0, 2.0),
    ],
)
def test_retry_after_is_bounded_and_malformed_headers_use_backoff(
    header, attempt, expected
):
    assert _retry_delay(header, attempt) == expected


async def test_tool_timeout_returns_structured_failure_and_cancels_the_read(
    monkeypatch,
):
    monkeypatch.setattr("loremaster.server.MAX_TOOL_SECONDS", 0.01)
    cancelled = False

    async def slow_read():
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    client = KankaClient(
        "dummy",
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected HTTP")),
    )
    monkeypatch.setattr(client, "list_campaigns", slow_read)
    try:
        server = create_server(client)
        result = await server.call_tool("list_campaigns", {})
        assert result.isError is True and cancelled
        assert result.structuredContent["data"] is None
        assert result.structuredContent["complete"] is False
        assert result.structuredContent["error"]["code"] == "operation_timeout"
        monkeypatch.setattr(client, "list_campaigns", AsyncMock(return_value=[]))
        recovered = await server.call_tool("list_campaigns", {})
        assert not recovered.isError and recovered.structuredContent["complete"] is True
    finally:
        await client.aclose()
