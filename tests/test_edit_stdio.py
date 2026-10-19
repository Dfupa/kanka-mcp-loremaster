"""Actual SDK stdio + separate local approval subprocess, with mocked HTTP."""

import asyncio
import json
import os
import pty
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loremaster.edit_store import EditStore

ROOT = Path(__file__).resolve().parents[1]


def approve_edit(database, proposal):
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("KANKA_")
    }
    command = [
        sys.executable,
        "-m",
        "loremaster.approve",
        "--database",
        str(database),
        "--kind",
        "edit",
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
    assert inspected["plan"] == proposal["plan"]
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
        os.write(
            master,
            f"approve {proposal['proposal_id']} {proposal['review_digest']}\n".encode(),
        )
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "Approved for five minutes" in stdout
    finally:
        os.close(master)
        os.close(slave)


async def test_expanded_character_edit_workflow_uses_real_stdio_and_local_cli(tmp_path):
    database = tmp_path / "private.sqlite3"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "edit_stdio_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-edit-stdio-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_CREATION_DATABASE": str(database),
            "KANKA_WRITE_CAMPAIGN_IDS": "7",
            "KANKA_WRITE_OPERATIONS": (
                "character.update,post.update,inventory.update,"
                "relation.update,tag.update,race.update"
            ),
        },
    )
    proposals = []
    cases = [
        (
            "prepare_character_edit",
            {},
            {
                "name": "Élan II",
                "entry": "<p>Revised Élan lore.</p>",
                "age": "29",
                "sex": "Non-binary",
                "race_entity_ids": [202],
                "location_entity_ids": [302],
            },
        ),
        ("prepare_tag_edit", {"entity_tag_id": 91}, {"tag_entity_id": 402}),
        (
            "prepare_post_edit",
            {"post_id": 61},
            {"name": "Revised post", "entry": "<p>Revised first note.</p>"},
        ),
        (
            "prepare_inventory_edit",
            {"inventory_id": 71},
            {
                "amount": 3,
                "is_equipped": False,
                "position": "Pack",
                "item_entity_id": 502,
            },
        ),
        (
            "prepare_relation_edit",
            {"relation_id": 81},
            {"relation": "Trusted friend", "attitude": 50, "is_pinned": True},
        ),
        (
            "prepare_species_edit",
            {"entity_id": 201},
            {"entry": "A revised synthetic species description."},
        ),
    ]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        async with asyncio.timeout(45):
            async with stdio_client(params, errlog=log) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert len(tools) == 34
                    assert not any(
                        "approve" in tool.name or "reconcile" in tool.name
                        for tool in tools
                    )
                    for tool, ids, changes in cases:
                        prepared = await session.call_tool(
                            tool,
                            {
                                "campaign_id": 7,
                                "entity_id": 101,
                                "changes": changes,
                                **ids,
                            },
                        )
                        assert not prepared.isError, prepared.structuredContent
                        proposal = prepared.structuredContent["data"]
                        proposals.append(proposal["proposal_id"])
                        args = {
                            "campaign_id": 7,
                            "proposal_id": proposal["proposal_id"],
                        }
                        denied = await session.call_tool("execute_edit", args)
                        assert (
                            denied.isError
                            and denied.structuredContent["error"]["code"]
                            == "approval_required"
                        )
                        approve_edit(database, proposal)
                        committed = await session.call_tool("execute_edit", args)
                        assert not committed.isError, committed.structuredContent
                        assert (
                            committed.structuredContent["data"]["status"] == "verified"
                        )
                        status = await session.call_tool("get_edit_status", args)
                        assert status.structuredContent["data"]["status"] == "complete"
                        assert (
                            status.structuredContent["data"]["outcome"] == "confirmed"
                        )
                        assert "plan" not in status.structuredContent["data"]
                        replay = await session.call_tool("execute_edit", args)
                        assert replay.isError
                    actual = await session.call_tool(
                        "get_entity", {"campaign_id": 7, "entity_id": 101}
                    )
                    details = actual.structuredContent["data"]["details"]
                    assert details["name"] == "Élan II" and details["age"] == "29"
                    assert details["races"] == [22] and details["locations"] == [32]
                    assert details["tags"] == [42] and details["title"] == "Scout"
                    posts = await session.call_tool(
                        "get_entity_posts", {"campaign_id": 7, "entity_id": 101}
                    )
                    assert (
                        posts.structuredContent["data"][0]["entry"]
                        == "<p>Revised first note.</p>"
                    )
        log.seek(0)
        diagnostics = log.read()
    assert "offline-edit-stdio-token" not in diagnostics
    report = json.loads(
        next(
            line.split("EDIT_FIXTURE_RESULT=", 1)[1]
            for line in diagnostics.splitlines()
            if "EDIT_FIXTURE_RESULT=" in line
        )
    )
    assert len(report["patches"]) == 6
    assert report["patches"][-1] == [
        "PATCH",
        "campaigns/7/races/21",
        {"entry": "A revised synthetic species description."},
    ]
    assert report["clients_closed"] is True
    assert report["attachments"]["posts"][1]["entry"] == "<p>Second.</p>"
    assert report["attachments"]["inventory"][1]["amount"] == 1
    assert report["attachments"]["relations"][1]["relation"] == "Apprentice"
    assert all(row[0] == "PATCH" for row in report["patches"])
    store = EditStore(database)
    try:
        assert all(store.review(pid)["status"] == "complete" for pid in proposals)
    finally:
        store.close()
    assert b"offline-edit-stdio-token" not in database.read_bytes()
