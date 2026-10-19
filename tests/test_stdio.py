"""Real subprocess MCP sessions with a synthetic HTTP boundary and no live secrets."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
RESULT_KEYS = {
    "data",
    "campaign_id",
    "entity_id",
    "source",
    "complete",
    "page",
    "next_page",
    "missing_data",
    "error",
}


def result_data(result):
    """Assert the same contract is available in text and structured MCP content."""
    assert result.structuredContent is not None
    assert set(result.structuredContent) == RESULT_KEYS
    assert json.loads(result.content[0].text) == result.structuredContent
    return result.structuredContent


async def test_stdio_initialize_eight_tools_reads_errors_recovery_and_shutdown():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "stdio_fixture_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-test-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
        },
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        async with asyncio.timeout(20):
            async with stdio_client(params, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "Kanka LoreMaster"
                    tools = (await session.list_tools()).tools
                    assert len(tools) == 8
                    for tool in tools:
                        assert tool.annotations.readOnlyHint is True
                        assert tool.annotations.destructiveHint is False
                        assert set(tool.outputSchema["required"]) == RESULT_KEYS

                    campaigns = result_data(
                        await session.call_tool("list_campaigns", {})
                    )
                    assert campaigns["data"] == [
                        {"id": 7, "name": "Synthetic campaign"}
                    ]
                    assert (
                        campaigns["campaign_id"] is None
                        and campaigns["complete"] is True
                    )
                    lookup = result_data(
                        await session.call_tool(
                            "find_entities_by_name", {"campaign_id": 7, "name": "Áster"}
                        )
                    )
                    assert lookup["data"]["exact"][0]["entity_id"] == 101
                    assert lookup["complete"] is True
                    article = result_data(
                        await session.call_tool(
                            "get_entity", {"campaign_id": 7, "entity_id": 101}
                        )
                    )
                    assert article["data"]["entity"]["name"] == "Áster"
                    assert "café" in article["data"]["details"]["entry"]
                    assert article["entity_id"] == 101
                    full = result_data(
                        await session.call_tool(
                            "get_entity_full", {"campaign_id": 7, "entity_id": 101}
                        )
                    )
                    assert full["complete"] is True
                    assert (
                        full["data"]["sections"]["posts"]["data"][0]["name"]
                        == "History"
                    )
                    assert full["data"]["sections"]["images"]["data"]["image"] is None
                    assert len(full["data"]["sections"]) == 11
                    search = result_data(
                        await session.call_tool(
                            "search_entities", {"campaign_id": 7, "query": "Áster"}
                        )
                    )
                    assert search["data"][0]["entity_id"] == 101
                    posts = result_data(
                        await session.call_tool(
                            "get_entity_posts", {"campaign_id": 7, "entity_id": 101}
                        )
                    )
                    assert posts["data"][0]["name"] == "History"
                    inventory = result_data(
                        await session.call_tool(
                            "get_entity_inventory", {"campaign_id": 7, "entity_id": 102}
                        )
                    )
                    assert inventory["data"][0]["name"] == "Compass"
                    assert (
                        inventory["complete"] is False and inventory["next_page"] == 2
                    )
                    last = result_data(
                        await session.call_tool(
                            "get_entity_inventory",
                            {"campaign_id": 7, "entity_id": 102, "page": 2},
                        )
                    )
                    assert last["next_page"] is None and last["complete"] is False
                    relations = result_data(
                        await session.call_tool(
                            "get_entity_relations", {"campaign_id": 7, "entity_id": 103}
                        )
                    )
                    assert len(relations["data"]) == 4
                    assert relations["data"][0]["target_name"] == "Áster"
                    assert relations["data"][2]["direction"] == "incoming"
                    assert relations["missing_data"][0]["reason"] == "unavailable"
                    assert relations["complete"] is False

                    denied = await session.call_tool(
                        "get_entity_inventory", {"campaign_id": 8, "entity_id": 102}
                    )
                    assert denied.isError is True
                    failure = result_data(denied)
                    assert failure["error"]["code"] == "campaign_not_allowed"
                    assert failure["data"] is None and failure["complete"] is False
                    malformed = await session.call_tool(
                        "get_entity", {"campaign_id": True, "entity_id": 101}
                    )
                    assert malformed.isError is True
                    unknown = await session.call_tool(
                        "delete_entity", {"campaign_id": 7, "entity_id": 101}
                    )
                    assert unknown.isError is True
                    recovered = await session.call_tool("list_campaigns", {})
                    assert (
                        not recovered.isError
                        and result_data(recovered)["complete"] is True
                    )
                    assert "PRIVATE UPSTREAM PAYLOAD" not in json.dumps(relations)
        errors.seek(0)
        stderr = errors.read()
        assert "HTTP_TRANSPORT_CLOSED" in stderr
        assert "offline-test-token" not in stderr
        assert "PRIVATE UPSTREAM PAYLOAD" not in stderr


def test_real_entrypoint_requires_a_token_without_exposing_credentials():
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("KANKA_")
    }
    entrypoint = Path(sys.executable).with_name("kanka-loremaster")
    assert entrypoint.exists()
    process = subprocess.run(
        [str(entrypoint)],
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert process.returncode != 0
    assert "Set KANKA_API_TOKEN" in process.stderr
    assert process.stdout == ""
