"""Shared synthetic creation fixture; a real HTTP attempt is a test failure."""

from unittest.mock import AsyncMock

import httpx
import pytest
from creation_fixtures import offline


@pytest.fixture
async def rehearsal(monkeypatch):
    network = AsyncMock(side_effect=AssertionError("Real networking is forbidden"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    simulation = offline.OfflineCreation()
    try:
        yield simulation
    finally:
        await simulation.aclose()
    network.assert_not_awaited()
    assert simulation._writer.is_closed
    assert simulation.reader._http.is_closed
