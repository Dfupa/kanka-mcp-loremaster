"""Shared success, error, payload and strict-input contracts at the MCP boundary."""

import json

import httpx
import pytest
from fake_kanka import envelope, handler
from mcp.server.fastmcp.exceptions import ToolError

from loremaster.kanka import KankaClient
from loremaster.server import create_server


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
async def test_upstream_failures_set_mcp_error_and_sanitized_structured_details(status):
    client = KankaClient(
        "secret-test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status, headers={"Retry-After": "60"}, text="PRIVATE PAYLOAD"
            )
        ),
    )
    try:
        server = create_server(client)
        result = await server.call_tool(
            "get_entity_inventory", {"campaign_id": 7, "entity_id": 102}
        )
        assert result.isError is True
        data = result.structuredContent
        assert data["data"] is None and data["complete"] is False
        assert data["campaign_id"] == 7 and data["entity_id"] == 102
        assert data["error"] == {
            "code": "upstream_http",
            "message": f"Kanka returned HTTP {status}",
            "status_code": status,
        }
        assert "PRIVATE" not in result.content[0].text
        assert "secret-test-token" not in result.content[0].text
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "method",
    ["get_entity", "get_entity_inventory", "get_entity_relations", "get_entity_posts"],
)
@pytest.mark.parametrize("bad_id", [True, 0, -1, 7.0, "7"])
async def test_malformed_mcp_identifiers_fail_before_http(method, bad_id):
    client = KankaClient(
        "dummy",
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected HTTP")),
    )
    try:
        server = create_server(client)
        with pytest.raises(ToolError):
            await server.call_tool(method, {"campaign_id": bad_id, "entity_id": 101})
    finally:
        await client.aclose()


async def test_large_results_fail_explicitly_instead_of_silently_truncating():
    def override(request):
        if "/characters/" in request.url.path:
            return envelope(
                {"id": 11, "entity_id": 101, "name": "Áster", "entry": "á" * 150_000}
            )
        return handler(request)

    client = KankaClient("dummy", transport=httpx.MockTransport(override))
    try:
        result = await create_server(client).call_tool(
            "get_entity", {"campaign_id": 7, "entity_id": 101}
        )
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == "result_too_large"
        assert result.structuredContent["data"] is None
        assert len(result.content[0].text.encode()) < 1000
    finally:
        await client.aclose()


async def test_unknown_modules_return_metadata_with_explicit_incompleteness():
    client = KankaClient(
        "dummy",
        transport=httpx.MockTransport(
            lambda request: envelope(
                {
                    "id": 101,
                    "campaign_id": 7,
                    "name": "Custom",
                    "type": "custom-module",
                    "child_id": 5,
                }
            )
        ),
    )
    try:
        result = await create_server(client).call_tool(
            "get_entity", {"campaign_id": 7, "entity_id": 101}
        )
        data = result.structuredContent
        assert not result.isError
        assert data["complete"] is False and data["data"]["details"] is None
        assert data["missing_data"][0]["reason"] == "unsupported_module"
        assert json.loads(result.content[0].text) == data
    finally:
        await client.aclose()
