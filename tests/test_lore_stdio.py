"""Real MCP stdio and separate local approval processes, with HTTP mocked."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from lore_fixture import KINDS, TARGETS
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_edit_stdio import approve_edit
from test_lore_entities import changes, new_draft
from test_supervised_stdio import approve_local

from loremaster.approvals import LocalApprovals
from loremaster.edit_store import EditStore

ROOT = Path(__file__).resolve().parents[1]


async def test_all_new_types_create_edit_and_read_back_through_stdio(tmp_path):
    database = tmp_path / "private.sqlite3"
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("KANKA_")
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests/lore_stdio_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-lore-stdio-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_CREATION_DATABASE": str(database),
            "KANKA_WRITE_CAMPAIGN_IDS": "7",
            "KANKA_WRITE_OPERATIONS": "post.create,post.update,"
            + ",".join(
                kind + suffix for kind in KINDS for suffix in (".create", ".update")
            ),
        },
    )
    proposals = []
    created_ids = []
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        async with asyncio.timeout(60):
            async with stdio_client(params, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert len(tools) == 34
                    assert not any(
                        "approve" in tool.name or "reconcile" in tool.name
                        for tool in tools
                    )
                    for kind in KINDS:
                        draft = new_draft(kind)
                        if kind in ("quest", "note"):
                            draft = draft.model_copy(
                                update={"parent_entity_id": TARGETS[kind][0]}
                            )
                        prepared = await session.call_tool(
                            "prepare_" + kind + "_creation",
                            {"draft": draft.model_dump(mode="json")},
                        )
                        assert not prepared.isError, prepared
                        proposal = prepared.structuredContent["data"]
                        args = {
                            "campaign_id": 7,
                            "proposal_id": proposal["proposal_id"],
                        }
                        assert (
                            await session.call_tool("execute_creation", args)
                        ).isError
                        approve_local(database, proposal, env)
                        response = await session.call_tool("execute_creation", args)
                        assert not response.isError, response
                        created = response.structuredContent["data"]
                        assert created["status"] == "verified"
                        uid = created["entity_id"]
                        assert uid != created["child_id"]
                        created_ids.append(uid)
                        proposals.append(("creation", proposal["proposal_id"]))
                        assert (
                            await session.call_tool("execute_creation", args)
                        ).isError

                        prepared = await session.call_tool(
                            "prepare_" + kind + "_edit",
                            {
                                "campaign_id": 7,
                                "entity_id": uid,
                                "changes": changes(kind),
                            },
                        )
                        assert not prepared.isError, prepared
                        proposal = prepared.structuredContent["data"]
                        args["proposal_id"] = proposal["proposal_id"]
                        assert (await session.call_tool("execute_edit", args)).isError
                        approve_edit(database, proposal)
                        response = await session.call_tool("execute_edit", args)
                        assert not response.isError, response
                        assert (
                            response.structuredContent["data"]["status"] == "verified"
                        )
                        proposals.append(("edit", proposal["proposal_id"]))
                        assert (await session.call_tool("execute_edit", args)).isError
                        readback = await session.call_tool(
                            "get_entity", {"campaign_id": 7, "entity_id": uid}
                        )
                        assert not readback.isError
                        details = readback.structuredContent["data"]["details"]
                        assert details["name"] == changes(kind)["name"]
                        assert details["entry"] == changes(kind)["entry"]
                        assert details["tags"] == [42] and details["is_private"] is True
                        if kind in ("quest", "note"):
                            assert details["parent_id"] == TARGETS[kind][0]
                        if kind in ("quest", "journal", "note"):
                            prepared = await session.call_tool(
                                "prepare_entity_post_creation",
                                {
                                    "draft": {
                                        "campaign_id": 7,
                                        "entity_id": uid,
                                        "name": "Test post",
                                        "entry": "<p>Initial.</p>",
                                        "visibility_id": 1,
                                    }
                                },
                            )
                            assert not prepared.isError, prepared
                            proposal = prepared.structuredContent["data"]
                            args["proposal_id"] = proposal["proposal_id"]
                            assert (
                                await session.call_tool("execute_creation", args)
                            ).isError
                            approve_local(database, proposal, env)
                            response = await session.call_tool("execute_creation", args)
                            assert not response.isError, response
                            result = response.structuredContent["data"]
                            assert (
                                result["status"] == "verified"
                                and result["entity_id"] == uid
                            )
                            assert "child_id" not in result
                            proposals.append(("creation", proposal["proposal_id"]))
                            assert (
                                await session.call_tool("execute_creation", args)
                            ).isError
                            prepared = await session.call_tool(
                                "prepare_post_edit",
                                {
                                    "campaign_id": 7,
                                    "entity_id": uid,
                                    "post_id": result["post_id"],
                                    "changes": {"entry": "<p>Edited post.</p>"},
                                },
                            )
                            assert not prepared.isError, prepared
                            proposal = prepared.structuredContent["data"]
                            args["proposal_id"] = proposal["proposal_id"]
                            approve_edit(database, proposal)
                            response = await session.call_tool("execute_edit", args)
                            assert not response.isError, response
                            assert (
                                response.structuredContent["data"]["status"]
                                == "verified"
                            )
                            proposals.append(("edit", proposal["proposal_id"]))
                            posts = await session.call_tool(
                                "get_entity_posts", {"campaign_id": 7, "entity_id": uid}
                            )
                            assert not posts.isError
                            assert (
                                posts.structuredContent["data"][0]["entry"]
                                == "<p>Edited post.</p>"
                            )
                            core = await session.call_tool(
                                "get_entity", {"campaign_id": 7, "entity_id": uid}
                            )
                            assert (
                                core.structuredContent["data"]["details"]["entry"]
                                == changes(kind)["entry"]
                            )
        errors.seek(0)
        logs = errors.read()
    report = json.loads(
        next(
            line.removeprefix("LORE_FIXTURE_RESULT=")
            for line in logs.splitlines()
            if line.startswith("LORE_FIXTURE_RESULT=")
        )
    )
    assert report["clients_closed"]
    assert len(report["mutations"]) == 16
    assert sum(method == "POST" for method, _, _ in report["mutations"]) == 8
    assert len(set(created_ids)) == 5
    assert "offline-lore-stdio-token" not in logs
    assert b"offline-lore-stdio-token" not in database.read_bytes()
    creation, editing = LocalApprovals(database), EditStore(database)
    try:
        for kind, proposal_id in proposals:
            store = creation if kind == "creation" else editing
            assert store.review(proposal_id)["status"] == "complete"
    finally:
        creation.close()
        editing.close()
