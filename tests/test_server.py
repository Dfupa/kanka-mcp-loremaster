"""Exercise tool registration, dispatch and cleanup with the installed MCP SDK."""

import httpx
import pytest

from loremaster.kanka import KankaClient
from loremaster.server import create_server


@pytest.mark.parametrize("fail_during_lifespan", [False, True])
async def test_sdk_registers_tools_dispatches_reads_and_closes_client(
    fail_during_lifespan,
):
    requests = []

    def handler(request):
        assert request.method == "GET"
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={"data": [{"id": 7, "name": "Synthetic"}], "meta": {"last_page": 1}},
        )

    client = KankaClient("test-token", transport=httpx.MockTransport(handler))
    server = create_server(client)
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "list_campaigns",
        "search_entities",
        "find_entities_by_name",
        "get_entity",
        "get_entity_posts",
        "get_entity_inventory",
        "get_entity_relations",
    }
    lookup = next(tool for tool in tools if tool.name == "find_entities_by_name")
    assert set(lookup.inputSchema["required"]) == {"campaign_id", "name"}

    async def exercise():
        async with server._mcp_server.lifespan(server._mcp_server):
            assert not client._http.is_closed
            result = await server.call_tool("list_campaigns", {})
            assert not result.isError
            assert result.structuredContent["data"] == [{"id": 7, "name": "Synthetic"}]
            assert result.structuredContent["complete"] is True
            assert result.structuredContent["error"] is None
            if fail_during_lifespan:
                raise RuntimeError("Synthetic shutdown")

    if fail_during_lifespan:
        with pytest.raises(RuntimeError, match="Synthetic shutdown"):
            await exercise()
    else:
        await exercise()
    assert requests == ["/1.0/campaigns"]
    assert client._http.is_closed
