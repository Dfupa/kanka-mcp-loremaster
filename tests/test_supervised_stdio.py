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
                    assert len(tools) == 11
                    assert (
                        sum(tool.annotations.readOnlyHint is False for tool in tools)
                        == 2
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
                    command = [
                        sys.executable,
                        "-m",
                        "loremaster.approve",
                        "--database",
                        str(database),
                    ]
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
                        phrase = (
                            f"approve {proposal['proposal_id']} "
                            f"{proposal['review_digest']}\n"
                        )
                        os.write(master, phrase.encode())
                        stdout, stderr = process.communicate(timeout=10)
                        assert process.returncode == 0, stderr
                        assert "Approved for five minutes" in stdout
                    finally:
                        os.close(master)
                        os.close(slave)
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
