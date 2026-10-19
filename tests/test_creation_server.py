"""Accept the frozen created scenario through the installed SDK and real stdio."""

import asyncio
import json
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest
from creation_fixtures import approved, offline
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loremaster.server import create_server

ROOT = Path(__file__).resolve().parents[1]


def envelope(result):
    assert not result.isError
    data = result.structuredContent
    assert data["error"] is None
    assert data["complete"] is True
    assert data["missing_data"] == []
    assert data == json.loads(result.content[0].text)
    return data["data"]


async def check_created_tools(call_tool):
    """Compare fixed expectations independently of the creator's status labels."""
    matches = envelope(
        await call_tool(
            "find_entities_by_name",
            {
                "campaign_id": 7,
                "name": "LoreMaster test — Élan",
            },
        )
    )["exact"]
    assert len(matches) == 1
    uid = matches[0]["entity_id"]
    assert type(uid) is int and uid > 0
    args = {"campaign_id": 7, "entity_id": uid}
    core = envelope(await call_tool("get_entity", args))
    posts = envelope(await call_tool("get_entity_posts", args))
    inventory = envelope(await call_tool("get_entity_inventory", args))
    relations = envelope(await call_tool("get_entity_relations", args))
    full = envelope(
        await call_tool(
            "get_entity_full",
            {
                **args,
                "sections": ["posts", "inventory", "relations"],
            },
        )
    )
    expected = {
        "name": "LoreMaster test — Élan",
        "entry": "<p>Synthetic character created for an offline rehearsal.</p>",
        "is_private": True,
        "posts": [
            {
                "name": "Test post one",
                "entry": "<p>First synthetic note.</p>",
                "visibility_id": 3,
            },
            {
                "name": "Test post two",
                "entry": "<p>Second synthetic note.</p>",
                "visibility_id": 3,
            },
        ],
        "inventory": [
            {"item_id": 21, "amount": "2", "is_equipped": True, "position": "Belt"}
        ],
        "relations": [
            {
                "owner_id": uid,
                "target_id": 101,
                "relation": "Mentor",
                "attitude": 25,
                "visibility_id": 3,
                "direction": "outgoing",
            }
        ],
    }
    assert core["entity"]["id"] == core["details"]["entity_id"] == uid
    assert core["entity"]["child_id"] == core["details"]["id"] != uid
    observed = {key: core["details"][key] for key in ("name", "entry", "is_private")}
    for key, records in (
        ("posts", posts),
        ("inventory", inventory),
        ("relations", relations),
    ):
        observed[key] = [
            {field: row[field] for field in expected[key][0]} for row in records
        ]
        assert full["sections"][key]["data"] == records
        assert full["sections"][key]["complete"] is True
    assert observed == expected
    assert full["details"] == core["details"]
    assert {row["entity_id"] for row in posts + inventory} == {uid}
    assert len({row["id"] for row in posts}) == 2
    assert inventory[0]["item"]["entity_id"] == 201
    assert inventory[0]["item"]["id"] == 21
    assert relations[0]["target_name"] == "Synthetic mentor"
    return {
        "scenario": "synthetic-character-v1",
        "environment": "offline",
        "result": "PASS",
        "entity_id": uid,
        "child_id": core["details"]["id"],
        "expected": expected,
        "observed": observed,
        "post_ids": [row["id"] for row in posts],
        "sources": full["sources"],
        "complete": True,
        "tested_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=ROOT,
                text=True,
            ).strip()
        ),
    }


async def test_created_scenario_via_sdk_dispatch_and_seed_isolation(rehearsal):
    baseline = deepcopy(rehearsal.records)
    attachments = deepcopy(rehearsal.attachments)
    draft = offline.example_draft()
    preview = rehearsal.prepare(draft)
    assert rehearsal.requests == []
    grant = rehearsal.gate.approve(
        preview["proposal_id"],
        reviewed_digest=preview["content_digest"],
        approved_by="offline-test-reviewer",
    )
    result = await rehearsal.run(grant)
    assert grant.token not in json.dumps(result)
    report = await check_created_tools(create_server(rehearsal.reader).call_tool)
    for uid, record in baseline.items():
        assert rehearsal.records[uid] == record
        assert rehearsal.attachments.get(uid) == attachments.get(uid)
    assert [row for row in rehearsal.requests if row[0] != "GET"] == [
        ("POST", "campaigns/7/characters"),
        *[
            ("POST", f"campaigns/7/entities/{result['entity_id']}/{section}")
            for section in ("relations", "inventory", "posts", "posts")
        ],
    ]
    await rehearsal.aclose()
    assert rehearsal._writer.is_closed and rehearsal.reader._http.is_closed
    report.update(
        seed_isolation="PASS",
        clients_closed=True,
        mutation_counts={"POST": 5, "other": 0},
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


async def test_created_scenario_over_actual_stdio_and_shutdown():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "creation_stdio_server.py")],
        cwd=str(ROOT),
        env={"KANKA_API_TOKEN": "unused-offline-token"},
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        async with asyncio.timeout(20):
            async with stdio_client(params, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert len(tools) == 8
                    assert all(tool.annotations.readOnlyHint for tool in tools)
                    report = await check_created_tools(session.call_tool)
                    rejected = await session.call_tool(
                        "create_entity", {"campaign_id": 7}
                    )
                    assert rejected.isError
        errors.seek(0)
        stderr = errors.read()
        assert "CREATION_SEEDS_UNCHANGED FIVE_POSTS_ONLY CLIENTS_CLOSED" in stderr
        assert "unused-offline-token" not in stderr
    report.update(
        seed_isolation="PASS",
        clients_closed=True,
        mutation_counts={"POST": 5, "other": 0},
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


@pytest.mark.parametrize("failure", ["rejected", "uncertain", "mismatch"])
async def test_failed_creation_is_observable_through_mcp_without_retry(
    rehearsal, failure
):
    draft = offline.example_draft()
    if failure == "rejected":
        rehearsal.failure = ("post:1", 403)
    elif failure == "uncertain":
        rehearsal.lose_response = "post:0"
    result = await rehearsal.run(approved(rehearsal, draft))
    uid = result["entity_id"]
    if failure == "mismatch":
        rehearsal.attachments[uid]["posts"][1]["entry"] = "Unexpected content"
    trace = list(rehearsal.requests)
    read = await create_server(rehearsal.reader).call_tool(
        "get_entity_posts", {"campaign_id": 7, "entity_id": uid}
    )
    posts = envelope(read)
    if failure == "mismatch":
        assert posts[1]["entry"] == "Unexpected content"
    else:
        assert result["status"] == ("partial" if failure == "rejected" else "uncertain")
        assert len(posts) == 1
    assert all(method == "GET" for method, _path in rehearsal.requests[len(trace) :])
