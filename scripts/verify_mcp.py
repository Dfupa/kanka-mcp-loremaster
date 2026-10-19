"""Run the three live demonstrations through a real stdio MCP connection.

Use KANKA_API_TOKEN from the environment. Output is fixed pass/fail diagnostics;
credentials, campaign identifiers, names, descriptions and collections are never
printed or saved. No Kanka write tools or non-GET HTTP operations are available.
"""

import asyncio
import os
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

NAMES = ("Marcian", "Roderick", "Elgith")
FALLBACK_CAMPAIGN_ID = 33742
MAX_TOOL_CALLS = 50
MAX_PAGES = 20


async def verify_session(session: ClientSession) -> None:
    """Discover identities and verify descriptions, inventory and relation reads.

    Raise RuntimeError on ambiguous identities, MCP/API errors, incomplete
    pagination, missing linked data or malformed responses. Never quote payloads.
    """
    calls = 0

    async def call(tool: str, **arguments: Any) -> dict[str, Any]:
        nonlocal calls
        if calls >= MAX_TOOL_CALLS:
            raise RuntimeError("MCP request budget exceeded")
        calls += 1
        await asyncio.sleep(2.1)
        result = await session.call_tool(tool, arguments)
        data = result.structuredContent
        if result.isError or not isinstance(data, dict) or data.get("error"):
            raise RuntimeError("MCP read failed")
        missing = data.get("missing_data", [])
        if missing and not (
            tool == "search_entities"
            and all(item.get("reason") == "pagination_unavailable" for item in missing)
        ):
            raise RuntimeError("MCP read has missing data")
        return data

    async def collection(tool: str, campaign_id: int, entity_id: int) -> None:
        for page in range(1, MAX_PAGES + 1):
            result = await call(
                tool, campaign_id=campaign_id, entity_id=entity_id, page=page
            )
            if result.get("page") != page or not isinstance(result.get("data"), list):
                raise RuntimeError("Invalid collection result")
            following = result.get("next_page")
            if following is None:
                if page == 1 and result.get("complete") is not True:
                    raise RuntimeError("Incomplete collection")
                return
            if following != page + 1:
                raise RuntimeError("Unexpected collection pagination")
        raise RuntimeError("MCP pagination safety limit exceeded")

    tools = (await session.list_tools()).tools
    expected = {
        "list_campaigns",
        "find_entities_by_name",
        "search_entities",
        "get_entity",
        "get_entity_full",
        "get_entity_posts",
        "get_entity_inventory",
        "get_entity_relations",
    }
    if {tool.name for tool in tools} != expected:
        raise RuntimeError("Unexpected MCP tool registration")
    if any(
        tool.annotations is None or tool.annotations.readOnlyHint is not True
        for tool in tools
    ):
        raise RuntimeError("Read-only annotations are missing")
    campaigns = await call("list_campaigns")
    if campaigns.get("complete") is not True:
        raise RuntimeError("Incomplete campaign discovery")
    campaign_ids = list(dict.fromkeys(row["id"] for row in campaigns["data"]))
    if len(campaign_ids) > 5:
        raise RuntimeError("Campaign scope exceeds the smoke-check limit")

    async def candidates(campaign_id: int) -> list[dict[str, Any]] | None:
        selected = []
        for name in NAMES:
            result = await call(
                "find_entities_by_name", campaign_id=campaign_id, name=name
            )
            if result.get("complete") is not True:
                raise RuntimeError("Incomplete identity search")
            exact = result["data"]["exact"]
            if len(exact) > 1:
                raise RuntimeError("Character identity is ambiguous")
            if len(exact) != 1 or exact[0].get("entity_type") != "character":
                return None
            selected.append(exact[0])
        return selected

    matches = []
    for campaign_id in campaign_ids:
        selected = await candidates(campaign_id)
        if selected is not None:
            matches.append((campaign_id, selected))
    if not matches and FALLBACK_CAMPAIGN_ID not in campaign_ids:
        selected = await candidates(FALLBACK_CAMPAIGN_ID)
        if selected is not None:
            matches.append((FALLBACK_CAMPAIGN_ID, selected))
    if len(matches) != 1:
        raise RuntimeError("Campaign or character identity is missing or ambiguous")
    campaign_id, selected = matches[0]
    for candidate in selected:
        record = await call(
            "get_entity", campaign_id=campaign_id, entity_id=candidate["entity_id"]
        )
        entity = record["data"]["entity"]
        if (
            record.get("complete") is not True
            or entity["child_id"] != candidate["child_id"]
            or entity["id"] != candidate["entity_id"]
            or entity["campaign_id"] != campaign_id
            or entity["name"] != candidate["name"]
        ):
            raise RuntimeError("Incomplete or changed character identity")
        if candidate is selected[0]:
            entry = record["data"]["details"].get("entry")
            if not isinstance(entry, str) or not entry.strip():
                raise RuntimeError("Character description is unavailable")
    print("Description check: PASS")
    await collection("get_entity_inventory", campaign_id, selected[1]["entity_id"])
    print("Inventory check: PASS")
    await collection("get_entity_relations", campaign_id, selected[2]["entity_id"])
    print("Relations check: PASS")
    await call("search_entities", campaign_id=campaign_id, query=NAMES[0])
    await collection("get_entity_posts", campaign_id, selected[0]["entity_id"])
    print("Search and posts check: PASS")
    aggregate = await call(
        "get_entity_full",
        campaign_id=campaign_id,
        entity_id=selected[0]["entity_id"],
        sections=["posts"],
    )
    if (
        aggregate.get("complete") is not True
        or not aggregate["data"]["sections"]["posts"]["complete"]
    ):
        raise RuntimeError("Incomplete entity aggregation")
    print("Aggregate posts check: PASS")


async def main() -> None:
    """Spawn the installed server, verify it, and close the MCP process cleanly."""
    token = os.environ.get("KANKA_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("FAIL: KANKA_API_TOKEN is not configured")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "loremaster.server"],
        env={
            "KANKA_API_TOKEN": token,
            "KANKA_ALLOWED_CAMPAIGN_IDS": os.environ.get(
                "KANKA_ALLOWED_CAMPAIGN_IDS", ""
            ),
        },
    )
    try:
        with open(os.devnull, "w") as errors:
            async with asyncio.timeout(240):
                async with stdio_client(parameters, errlog=errors) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        await verify_session(session)
    except Exception:
        raise SystemExit(
            "FAIL: MCP smoke check did not complete; no private diagnostics printed"
        ) from None
    print("PASS: stdio MCP read checks and shutdown completed")


if __name__ == "__main__":
    asyncio.run(main())
