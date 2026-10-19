"""MCP proposal → separate local terminal approval → MCP execution → read-back."""

import asyncio
import json
import os
import pty
import subprocess
import sys
import tempfile
from pathlib import Path

from creation_fixtures import offline
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def approve_local(database, proposal, env):
    """Act as a separate trusted local reviewer only for this mocked test."""
    command = [sys.executable, "-m", "loremaster.approve", "--database", str(database)]
    review = subprocess.run(
        [*command, "review", proposal["proposal_id"]],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    inspected = json.loads(review.stdout)
    assert inspected["review_digest"] == proposal["review_digest"]
    assert inspected["progress"] == {}
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            [*command, "approve", proposal["proposal_id"]],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        phrase = f"approve {proposal['proposal_id']} {proposal['review_digest']}\n"
        os.write(master, phrase.encode())
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "Approved for five minutes" in stdout
    finally:
        os.close(master)
        os.close(slave)
    return inspected


async def test_real_stdio_with_separate_local_review_process(tmp_path):
    database = tmp_path / "private.sqlite3"
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("KANKA_")
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "supervised_stdio_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-stdio-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_CREATION_DATABASE": str(database),
            "KANKA_WRITE_CAMPAIGN_IDS": "7",
            "KANKA_WRITE_OPERATIONS": (
                "character.create,relation.create,inventory.add,post.create"
            ),
        },
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        async with asyncio.timeout(30):
            async with stdio_client(params, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert len(tools) == 21
                    assert (
                        sum(tool.annotations.readOnlyHint is False for tool in tools)
                        == 12
                    )
                    assert not any(
                        "approve" in tool.name or "revoke" in tool.name
                        for tool in tools
                    )
                    draft = offline.example_draft().model_copy(
                        update={
                            "age": "28",
                            "sex": "Male",
                            "race_entity_ids": (301,),
                            "location_entity_ids": (401,),
                        }
                    )
                    result = await session.call_tool(
                        "prepare_character_creation", draft.model_dump(mode="json")
                    )
                    assert not result.isError
                    proposal = result.structuredContent["data"]
                    args = {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
                    denied = await session.call_tool("execute_character_creation", args)
                    assert (
                        denied.isError
                        and denied.structuredContent["error"]["code"]
                        == "approval_required"
                    )
                    approve_local(database, proposal, env)
                    committed = await session.call_tool(
                        "execute_character_creation", args
                    )
                    assert not committed.isError
                    envelope = committed.structuredContent
                    assert envelope["complete"] is True
                    assert envelope["data"]["status"] == "verified"
                    uid = envelope["data"]["entity_id"]
                    assert uid != envelope["data"]["child_id"]
                    full = await session.call_tool(
                        "get_entity_full",
                        {
                            "campaign_id": 7,
                            "entity_id": uid,
                            "sections": ["posts", "inventory", "relations"],
                        },
                    )
                    assert full.structuredContent["complete"]
                    data = full.structuredContent["data"]
                    assert data["details"]["entry"] == draft.entry
                    assert data["details"]["is_private"] is True
                    assert data["details"]["age"] == "28"
                    assert data["details"]["sex"] == "Male"
                    assert data["details"]["races"] == [31]
                    assert data["details"]["locations"] == [41]
                    assert len(data["sections"]["posts"]["data"]) == 2
                    assert data["sections"]["inventory"]["data"][0]["amount"] == 2
                    assert (
                        data["sections"]["inventory"]["data"][0]["item"]["entity_id"]
                        == 201
                    )
                    status = await session.call_tool("get_creation_status", args)
                    assert status.structuredContent["data"]["status"] == "complete"
                    replay = await session.call_tool("execute_character_creation", args)
                    assert replay.isError
        errors.seek(0)
        stderr = errors.read()
        assert "SUPERVISED_POST_COUNT=5 CLIENTS_CLOSED" in stderr
        assert "offline-stdio-token" not in stderr


async def test_entire_synthetic_workflow_uses_mcp_and_local_approvals(tmp_path):
    """Mentor → item → Éldan → organisation, using returned IDs at each step."""
    database = tmp_path / "workflow.sqlite3"
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("KANKA_")
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "supervised_stdio_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-workflow-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_CREATION_DATABASE": str(database),
            "KANKA_WRITE_CAMPAIGN_IDS": "7",
            "KANKA_WRITE_OPERATIONS": (
                "character.create,relation.create,inventory.add,post.create,"
                "organisation.create,organisation.member.add,item.create"
            ),
        },
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        async with asyncio.timeout(45):
            async with stdio_client(params, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()

                    async def create(tool, payload):
                        result = await session.call_tool(
                            tool, {"campaign_id": 7, **payload}
                        )
                        assert not result.isError
                        proposal = result.structuredContent["data"]
                        args = {
                            "campaign_id": 7,
                            "proposal_id": proposal["proposal_id"],
                        }
                        denied = await session.call_tool("execute_creation", args)
                        assert (
                            denied.isError
                            and denied.structuredContent["error"]["code"]
                            == "approval_required"
                        )
                        approve_local(database, proposal, env)
                        committed = await session.call_tool("execute_creation", args)
                        assert not committed.isError
                        assert committed.structuredContent["complete"]
                        result = committed.structuredContent["data"]
                        assert result["status"] == "verified"
                        assert result["entity_id"] != result["child_id"]
                        status = await session.call_tool("get_creation_status", args)
                        assert status.structuredContent["data"]["status"] == "complete"
                        return result, status.structuredContent["data"]

                    mentor, _status = await create(
                        "prepare_character_creation",
                        {
                            "name": "Workflow synthetic mentor",
                            "entry": "<p>Synthetic mentor.</p>",
                            "is_private": False,
                        },
                    )
                    item, _status = await create(
                        "prepare_item_creation",
                        {
                            "name": "Workflow synthetic compass",
                            "entry": "<p>Synthetic compass.</p>",
                            "is_private": False,
                            "type": "Tool",
                            "price": "1 gp",
                            "size": "Small",
                            "weight": "0.1 kg",
                        },
                    )
                    item_read = await session.call_tool(
                        "get_entity", {"campaign_id": 7, "entity_id": item["entity_id"]}
                    )
                    assert (
                        item_read.structuredContent["data"]["details"]["type"] == "Tool"
                    )
                    assert (
                        item_read.structuredContent["data"]["details"]["price"]
                        == "1 gp"
                    )
                    assert (
                        item_read.structuredContent["data"]["details"]["is_private"]
                        is False
                    )
                    character, _status = await create(
                        "prepare_character_creation",
                        {
                            "name": "Synthetic Éldan",
                            "entry": "<p>Synthetic workflow character.</p>",
                            "is_private": False,
                            "age": "28",
                            "sex": "Male",
                            "race_entity_ids": [301],
                            "location_entity_ids": [401],
                            "relations": [
                                {
                                    "target_id": mentor["entity_id"],
                                    "relation": "Mentor",
                                    "visibility_id": 1,
                                }
                            ],
                            "inventory": [
                                {
                                    "item_entity_id": item["entity_id"],
                                    "amount": 2,
                                    "is_equipped": True,
                                    "position": "Belt",
                                }
                            ],
                            "posts": [
                                {
                                    "name": "Test post one",
                                    "entry": "<p>First synthetic note.</p>",
                                    "visibility_id": 1,
                                },
                                {
                                    "name": "Test post two",
                                    "entry": "<p>Second synthetic note.</p>",
                                    "visibility_id": 1,
                                },
                            ],
                        },
                    )
                    organisation, status = await create(
                        "prepare_organisation_creation",
                        {
                            "name": "Workflow synthetic organisation",
                            "entry": "<p>Both synthetic characters are members.</p>",
                            "is_private": False,
                            "type": "Guild",
                            "members": [
                                {
                                    "character_entity_id": character["entity_id"],
                                    "role": "Member",
                                    "is_private": False,
                                },
                                {
                                    "character_entity_id": mentor["entity_id"],
                                    "role": "Member",
                                    "is_private": False,
                                },
                            ],
                        },
                    )
                    assert (
                        status["progress"]["member:0"]["character_id"]
                        == character["child_id"]
                    )
                    assert (
                        status["progress"]["member:1"]["character_id"]
                        == mentor["child_id"]
                    )
                    assert all(
                        status["progress"][f"member:{index}"]["organisation_id"]
                        == organisation["child_id"]
                        for index in range(2)
                    )
                    full = await session.call_tool(
                        "get_entity_full",
                        {
                            "campaign_id": 7,
                            "entity_id": character["entity_id"],
                            "sections": ["posts", "inventory", "relations"],
                        },
                    )
                    assert full.structuredContent["complete"]
                    bundle = full.structuredContent["data"]
                    assert bundle["details"]["age"] == "28"
                    assert len(bundle["sections"]["posts"]["data"]) == 2
                    assert (
                        bundle["sections"]["inventory"]["data"][0]["item_id"]
                        == item["child_id"]
                    )
                    assert (
                        bundle["sections"]["relations"]["data"][0]["target_id"]
                        == mentor["entity_id"]
                    )
                    assert organisation["completed"] == [
                        "organisation",
                        "member:0",
                        "member:1",
                    ]
        errors.seek(0)
        stderr = errors.read()
        assert "SUPERVISED_POST_COUNT=10 CLIENTS_CLOSED" in stderr
        assert "offline-workflow-token" not in stderr
