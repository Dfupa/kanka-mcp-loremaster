"""Mock the Kanka boundary while exercising host approval and MCP execution."""

import asyncio
import json

import httpx
import pytest
from creation_fixtures import offline
from mcp.server.fastmcp.exceptions import ToolError
from supervised_fixture import handle

from loremaster import approvals, approve
from loremaster.approvals import LocalApprovals
from loremaster.creation import CreationScope
from loremaster.execution import CreationService
from loremaster.kanka import KankaClient
from loremaster.server import create_server, creation_settings
from loremaster.server import main as start_server

ALL_OPERATIONS = frozenset(
    {"character.create", "relation.create", "inventory.add", "post.create"}
)


@pytest.fixture
async def service(rehearsal, tmp_path):
    def handler(request):
        return handle(rehearsal, request)

    reader = KankaClient(
        "offline-test-token",
        allowed_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(handler),
    )
    store = LocalApprovals(tmp_path / "private.sqlite3")
    service = CreationService(
        reader,
        "offline-test-token",
        store,
        CreationScope(campaign_ids=frozenset({7}), operations=ALL_OPERATIONS),
        transport=httpx.MockTransport(handler),
    )
    try:
        yield service, rehearsal
    finally:
        await service.aclose()
        await reader.aclose()


def authorize(service, proposal):
    service.store.approve(
        proposal["proposal_id"], proposal["review_digest"], "trusted-local-reviewer"
    )


async def test_mcp_proposal_local_approval_execution_and_status(service):
    creator, api = service
    server = create_server(creator.reader, creator)
    tools = await server.list_tools()
    assert len(tools) == 11
    assert not any("approve" in tool.name or "revoke" in tool.name for tool in tools)
    draft = offline.example_draft()
    prepared = await server.call_tool(
        "prepare_character_creation", draft.model_dump(mode="json")
    )
    assert not prepared.isError
    proposal = prepared.structuredContent["data"]
    assert proposal["status"] == "pending"
    assert not any(method == "POST" for method, _ in api.requests)
    before = list(api.requests)
    args = {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
    denied = await server.call_tool("execute_character_creation", args)
    assert denied.isError
    assert denied.structuredContent["error"]["code"] == "approval_required"
    assert api.requests == before
    authorize(creator, proposal)
    created = await server.call_tool("execute_character_creation", args)
    assert not created.isError
    assert created.structuredContent["data"]["status"] == "verified"
    assert len([method for method, _ in api.requests if method == "POST"]) == 5
    status = await server.call_tool("get_creation_status", args)
    assert status.structuredContent["data"]["status"] == "complete"
    assert set(status.structuredContent["data"]["steps"].values()) == {"confirmed"}
    count = len(api.requests)
    replay = await server.call_tool("execute_character_creation", args)
    assert replay.isError and len(api.requests) == count
    assert "offline-test-token" not in json.dumps(created.structuredContent)


@pytest.mark.parametrize(
    "step,code",
    [("character", 403), ("relation:0", 403), ("inventory:0", 422), ("post:1", 403)],
)
async def test_known_rejection_resumes_only_after_new_local_approval(
    service, step, code
):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    api.failure = (step, code)
    first = await creator.execute(7, proposal["proposal_id"])
    assert first["status"] == ("failed" if step == "character" else "partial")
    assert first["failed_step"] == step
    before = list(api.requests)
    denied = await create_server(creator.reader, creator).call_tool(
        "execute_character_creation",
        {"campaign_id": 7, "proposal_id": proposal["proposal_id"]},
    )
    assert denied.isError and api.requests == before
    api.failure = None
    authorize(creator, proposal)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "verified"
    assert len(api.records) == 3
    assert len(api.attachments[result["entity_id"]]["posts"]) == 2
    assert "SYNTHETIC PRIVATE FAILURE" not in json.dumps(first)


@pytest.mark.parametrize("fault", ["lost", 500, 408, 302])
async def test_uncertainty_cannot_be_erased_by_reapproval(service, fault):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    if fault == "lost":
        api.lose_response = "post:0"
    else:
        api.failure = ("post:0", fault)
    first = await creator.execute(7, proposal["proposal_id"])
    assert first["status"] == "uncertain"
    before = list(api.requests)
    with pytest.raises(PermissionError, match="Reconcile uncertain"):
        authorize(creator, proposal)
    second = await create_server(creator.reader, creator).call_tool(
        "execute_character_creation",
        {"campaign_id": 7, "proposal_id": proposal["proposal_id"]},
    )
    assert second.isError
    assert second.structuredContent["error"]["code"] == "approval_required"
    assert api.requests == before


@pytest.mark.parametrize(
    "mode", ["expiry", "revocation", "tamper", "scope", "campaign"]
)
async def test_invalid_approval_content_and_scope_cannot_send(
    service, monkeypatch, mode
):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    if mode == "expiry":
        monkeypatch.setattr(
            approvals.time, "time", lambda: proposal["approval_expires_at"] or 10**12
        )
    elif mode == "revocation":
        creator.store.revoke(proposal["proposal_id"])
    elif mode == "tamper":
        with creator.store._db:
            creator.store._db.execute(
                "UPDATE proposals SET payload = '{}' WHERE id = ?",
                (proposal["proposal_id"],),
            )
    elif mode == "scope":
        creator.scope = CreationScope()
    before = list(api.requests)
    result = await create_server(creator.reader, creator).call_tool(
        "execute_character_creation",
        {
            "campaign_id": 8 if mode == "campaign" else 7,
            "proposal_id": proposal["proposal_id"],
        },
    )
    assert result.isError
    assert api.requests == before


async def test_renamed_reference_stops_before_first_post(service):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    api.records[101]["name"] = "Changed target"
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "failed"
    assert not any(method == "POST" for method, _ in api.requests)


def test_creation_is_disabled_without_explicit_configuration(monkeypatch):
    for name in (
        "KANKA_CREATION_DATABASE",
        "KANKA_WRITE_CAMPAIGN_IDS",
        "KANKA_WRITE_OPERATIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert creation_settings() is None
    monkeypatch.setenv("KANKA_WRITE_CAMPAIGN_IDS", "7")
    with pytest.raises(ValueError, match="requires"):
        creation_settings()


@pytest.mark.parametrize(
    "campaign,operations",
    [("0", "character.create"), ("7", "delete"), ("True", "character.create")],
)
def test_invalid_creation_configuration_rejected(
    monkeypatch, tmp_path, campaign, operations
):
    monkeypatch.setenv("KANKA_CREATION_DATABASE", str(tmp_path / "private.sqlite3"))
    monkeypatch.setenv("KANKA_WRITE_CAMPAIGN_IDS", campaign)
    monkeypatch.setenv("KANKA_WRITE_OPERATIONS", operations)
    with pytest.raises(ValueError):
        creation_settings()


@pytest.mark.parametrize("step", ["character", "relation:0", "inventory:0", "post:0"])
async def test_explicit_reconciliation_and_fresh_approval_resume_without_duplicates(
    service, step
):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    api.lose_response = step
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    uid = next(
        uid
        for uid, record in api.records.items()
        if record["name"] == offline.example_draft().name
    )
    section = {
        "relation:0": "relations",
        "inventory:0": "inventory",
        "post:0": "posts",
    }.get(step)
    candidate = uid if step == "character" else api.attachments[uid][section][0]["id"]
    before = len(api.requests)
    with pytest.raises(PermissionError, match="Reconcile uncertain"):
        authorize(creator, proposal)
    evidence = await creator.inspect_reconciliation(
        7, proposal["proposal_id"], candidate
    )
    assert evidence["key"] == step
    assert all(method == "GET" for method, _path in api.requests[before:])
    assert "uncertain" in creator.store.journal.progress(proposal["proposal_id"])
    creator.store.journal.confirm_reconciled(
        proposal["proposal_id"],
        evidence["fingerprint"],
        evidence["record"],
        reviewed_by="Trusted reviewer",
    )
    api.lose_response = None
    authorize(creator, proposal)
    completed = await creator.execute(7, proposal["proposal_id"])
    assert completed["status"] == "verified"
    assert len([method for method, _path in api.requests if method == "POST"]) == 5
    assert len(api.attachments[uid]["posts"]) == 2


@pytest.mark.parametrize("confirm", [True, False])
async def test_local_reconciliation_command_never_issues_approval(
    service, monkeypatch, tmp_path, confirm
):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    api.lose_response = "post:0"
    result = await creator.execute(7, proposal["proposal_id"])
    candidate = api.attachments[result["entity_id"]]["posts"][0]["id"]
    path = tmp_path / "private.sqlite3"
    monkeypatch.setenv("KANKA_CREATION_DATABASE", str(path))
    monkeypatch.setenv("KANKA_WRITE_CAMPAIGN_IDS", "7")
    monkeypatch.setenv("KANKA_WRITE_OPERATIONS", ",".join(sorted(ALL_OPERATIONS)))
    monkeypatch.setenv("KANKA_API_TOKEN", "offline-test-token")
    monkeypatch.setattr(
        approve, "KankaClient", lambda *_args, **_kwargs: creator.reader
    )
    monkeypatch.setattr(approve, "CreationService", lambda *_args, **_kwargs: creator)
    pending = creator.store.journal.pending(proposal["proposal_id"])
    phrase = f"reconcile {proposal['proposal_id']} {pending['fingerprint']}"
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: phrase if confirm else "cancel"
    )
    before = len(api.requests)
    if confirm:
        await approve.reconcile(creator.store, path, proposal["proposal_id"], candidate)
        assert "uncertain" not in creator.store.journal.progress(
            proposal["proposal_id"]
        )
    else:
        with pytest.raises(SystemExit, match="cancelled"):
            await approve.reconcile(
                creator.store, path, proposal["proposal_id"], candidate
            )
        assert "uncertain" in creator.store.journal.progress(proposal["proposal_id"])
    assert all(method == "GET" for method, _path in api.requests[before:])
    with pytest.raises(PermissionError, match="approval"):
        creator.store.claim(proposal["proposal_id"])


@pytest.mark.parametrize("fault", ["json", "id", "owner"])
async def test_unverifiable_success_preserves_intent_and_blocks_retry(service, fault):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)

    def invalid_success(request):
        response = handle(api, request)
        if request.method == "POST" and api._active_key == "post:0":
            if fault == "json":
                return httpx.Response(201, text="PRIVATE INVALID BODY")
            payload = response.json()
            payload["data"]["id" if fault == "id" else "entity_id"] = (
                True if fault == "id" else 999
            )
            return httpx.Response(201, json=payload)
        return response

    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/",
        transport=httpx.MockTransport(invalid_success),
    )
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    assert result["failed_step"] == "post:0"
    assert "PRIVATE INVALID BODY" not in json.dumps(result)
    assert len(api.attachments[result["entity_id"]]["posts"]) == 1


async def test_mcp_privacy_input_does_not_coerce_strings(service):
    creator, api = service
    args = offline.example_draft().model_dump(mode="json")
    args["is_private"] = "false"
    with pytest.raises(ToolError, match="valid boolean"):
        await create_server(creator.reader, creator).call_tool(
            "prepare_character_creation", args
        )
    assert api.requests == []


async def test_cancelled_send_retains_uncertainty(service):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    applied = asyncio.Event()

    async def interrupted(request):
        handle(api, request)
        applied.set()
        await asyncio.Event().wait()

    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(interrupted)
    )
    task = asyncio.create_task(creator.execute(7, proposal["proposal_id"]))
    async with asyncio.timeout(3):
        await applied.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (
        creator.store.journal.progress(proposal["proposal_id"])["uncertain"]
        == "character"
    )
    assert len(api.records) == 3
    before = list(api.requests)
    with pytest.raises(PermissionError, match="Reconcile uncertain"):
        authorize(creator, proposal)
    assert api.requests == before


async def test_revocation_after_a_success_stops_before_next_post(service):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)

    def revoke(request):
        response = handle(api, request)
        if request.method == "POST":
            creator.store.revoke(proposal["proposal_id"])
        return response

    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(revoke)
    )
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "partial"
    assert result["completed"] == ["character"]
    assert result["failed_step"] == "relation:0"
    assert sum(method == "POST" for method, _path in api.requests) == 1


@pytest.mark.parametrize("checkpoint", ["approved", "rejected", "uncertain"])
async def test_executor_restart_preserves_approval_progress_and_uncertainty(
    service, tmp_path, checkpoint
):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    if checkpoint == "rejected":
        api.failure = ("post:1", 403)
        assert (await creator.execute(7, proposal["proposal_id"]))[
            "status"
        ] == "partial"
    elif checkpoint == "uncertain":
        api.lose_response = "post:0"
        assert (await creator.execute(7, proposal["proposal_id"]))[
            "status"
        ] == "uncertain"
    await creator.aclose()
    restarted = CreationService(
        creator.reader,
        "offline-test-token",
        LocalApprovals(tmp_path / "private.sqlite3"),
        creator.scope,
        transport=httpx.MockTransport(lambda request: handle(api, request)),
    )
    try:
        if checkpoint == "uncertain":
            before = list(api.requests)
            assert (
                restarted.status(7, proposal["proposal_id"])["steps"]["post:0"]
                == "uncertain"
            )
            with pytest.raises(PermissionError, match="Reconcile uncertain"):
                authorize(restarted, proposal)
            assert api.requests == before
            return
        if checkpoint == "rejected":
            denied = await create_server(restarted.reader, restarted).call_tool(
                "execute_character_creation",
                {"campaign_id": 7, "proposal_id": proposal["proposal_id"]},
            )
            assert denied.isError
            api.failure = None
            authorize(restarted, proposal)
        result = await restarted.execute(7, proposal["proposal_id"])
        assert result["status"] == "verified"
        assert len(api.records) == 3
        assert len(api.attachments[result["entity_id"]]["posts"]) == 2
        expected_attempts = 6 if checkpoint == "rejected" else 5
        assert (
            sum(method == "POST" for method, _path in api.requests) == expected_attempts
        )
    finally:
        await restarted.aclose()


def test_startup_rejects_write_scope_outside_read_allowlist(monkeypatch, tmp_path):
    path = tmp_path / "private.sqlite3"
    monkeypatch.setenv("KANKA_API_TOKEN", "offline-test-token")
    monkeypatch.setenv("KANKA_ALLOWED_CAMPAIGN_IDS", "7")
    monkeypatch.setenv("KANKA_CREATION_DATABASE", str(path))
    monkeypatch.setenv("KANKA_WRITE_CAMPAIGN_IDS", "8")
    monkeypatch.setenv("KANKA_WRITE_OPERATIONS", "character.create")
    with pytest.raises(ValueError, match="read campaign allowlist"):
        start_server()
    assert not path.exists()
