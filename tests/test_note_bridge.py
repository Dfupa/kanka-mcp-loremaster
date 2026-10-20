"""Offline note handoffs reuse exact terminal approval and executor safeguards."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from lore_fixture import KINDS, LoreAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_supervised_stdio import approve_local

from loremaster.approvals import LocalApprovals
from loremaster.creation import CreationScope
from loremaster.edit_models import EDIT_OPERATIONS
from loremaster.edit_store import EditStore
from loremaster.editing import EditService
from loremaster.execution import CreationService
from loremaster.kanka import KankaClient, KankaError
from loremaster.note_bridge import SessionNoteBridge
from loremaster.note_models import ImportContext, NoteAnalysis, NotePlan
from loremaster.note_store import NoteStore, digest
from loremaster.server import create_server
from loremaster.session_notes import SessionNoteService

TEXT = (
    "Session 1 — 17/10/2026\n"
    "Élan reached the old bridge at age 45.\n"
    "Quest: Existing quest.\n"
    "New task: Recover the blue key.\n"
)


def analysis():
    return NoteAnalysis.model_validate(
        {
            "sessions": [
                {
                    "key": "s1",
                    "label": "Session 1",
                    "block_ids": [f"b-{i}" for i in range(1, 5)],
                    "date_anchor": {"block_id": "b-1", "excerpt": "17/10/2026"},
                }
            ],
            "analysed_blocks": [f"b-{i}" for i in range(1, 5)],
            "mentions": [
                {
                    "key": "hero",
                    "session_key": "s1",
                    "anchor": {"block_id": "b-2", "excerpt": "Élan"},
                    "name": "Élan",
                    "entity_type": "character",
                    "sections": ["posts", "inventory"],
                },
                {
                    "key": "quest",
                    "session_key": "s1",
                    "anchor": {"block_id": "b-3", "excerpt": "Existing quest"},
                    "name": "Existing quest",
                    "entity_type": "quest",
                    "sections": ["posts"],
                },
            ],
            "claims": [
                {
                    "key": "arrival",
                    "session_key": "s1",
                    "anchors": [
                        {
                            "block_id": "b-2",
                            "excerpt": "Élan reached the old bridge at age 45.",
                        }
                    ],
                    "text": "Élan reached the bridge at age 45.",
                    "subject": "hero",
                },
                {
                    "key": "goal",
                    "session_key": "s1",
                    "anchors": [
                        {"block_id": "b-4", "excerpt": "Recover the blue key."}
                    ],
                    "text": "Recover the blue key.",
                    "mode": "plan",
                },
            ],
        }
    )


def plan():
    common = {"session_key": "s1", "claim_keys": ["goal"]}
    return NotePlan.model_validate(
        {
            "assessments": [],
            "operations": [
                {
                    **common,
                    "key": "root",
                    "action": "create_entity",
                    "entity_type": "note",
                    "fields": {"name": "Synthetic session root"},
                },
                {
                    **common,
                    "key": "child",
                    "action": "create_entity",
                    "entity_type": "note",
                    "fields": {"name": "Synthetic child note"},
                    "references": [
                        {
                            "field": "parent_entity_id",
                            "target": {"kind": "proposal", "key": "root"},
                        }
                    ],
                },
                {
                    **common,
                    "key": "root-post",
                    "action": "create_post",
                    "entity_type": "post",
                    "fields": {"name": "Dated task"},
                    "target": {"kind": "proposal", "key": "root"},
                },
                {
                    **common,
                    "key": "existing-post",
                    "action": "create_post",
                    "entity_type": "post",
                    "fields": {"name": "Session evidence"},
                    "target": {"kind": "mention", "key": "quest"},
                },
                {
                    **common,
                    "key": "journal",
                    "action": "create_entity",
                    "entity_type": "journal",
                    "fields": {"name": "Synthetic session journal"},
                    "session_text": True,
                },
                {
                    **common,
                    "claim_keys": ["arrival"],
                    "key": "age",
                    "action": "edit_entity",
                    "entity_type": "character",
                    "fields": {"age": "45"},
                    "target": {"kind": "mention", "key": "hero"},
                },
                {
                    **common,
                    "claim_keys": ["arrival"],
                    "key": "post-edit",
                    "action": "edit_attachment",
                    "entity_type": "post",
                    "target": {"kind": "mention", "key": "hero"},
                    "record_id": 61,
                    "body": [
                        {"kind": "text", "text": "Élan reached the bridge at age 45."}
                    ],
                },
                {
                    **common,
                    "claim_keys": ["arrival"],
                    "key": "inventory-edit",
                    "action": "edit_attachment",
                    "entity_type": "inventory",
                    "target": {"kind": "mention", "key": "hero"},
                    "record_id": 71,
                    "fields": {"amount": 5},
                },
            ],
        }
    )


@pytest.fixture
async def bridge(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Real networking is forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    api = LoreAPI()
    api.records[101]["is_private"] = True
    reader = KankaClient(
        "offline-note-bridge",
        allowed_campaign_ids=frozenset({7}),
        admin_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(api.handle),
    )
    creator = CreationService(
        reader,
        "offline-note-bridge",
        LocalApprovals(tmp_path / "supervised.db"),
        CreationScope(
            campaign_ids=frozenset({7}),
            operations=frozenset(
                {*(kind + ".create" for kind in KINDS), "post.create"}
            ),
        ),
        transport=httpx.MockTransport(api.handle),
    )
    editor = EditService(
        reader,
        "offline-note-bridge",
        EditStore(tmp_path / "supervised.db"),
        frozenset({7}),
        EDIT_OPERATIONS,
        transport=httpx.MockTransport(api.handle),
    )
    notes = SessionNoteService(reader, NoteStore(tmp_path / "notes.db"))
    service = SessionNoteBridge(notes, creator, editor)
    try:
        yield service, api
    finally:
        notes.close()
        await editor.aclose()
        await creator.aclose()
        await reader.aclose()


async def preview(service, proposed=None):
    imported = service.notes.import_text(7, TEXT, ImportContext(locale="dmy"))
    data = await service.notes.submit(7, imported["import_id"], 0, analysis())
    while data["work_cursor"]:
        data = await service.notes.submit(
            7, imported["import_id"], data["version"], continuation=data["work_cursor"]
        )
    return await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        proposed or plan(),
    )


async def handoff(service, data, key):
    return await service.prepare(
        7, data["import_id"], data["version"], data["preview_digest"], key
    )


async def execute(service, link):
    supervisor = service.creation if link["service"] == "creation" else service.editing
    supervisor.store.approve(
        link["proposal_id"], link["review_digest"], "Offline terminal reviewer"
    )
    result = await supervisor.execute(7, link["proposal_id"])
    assert result["status"] == "verified", result
    return result


async def test_execution_overview_isolates_uncertainty_and_keeps_independent_work_ready(
    bridge,
):
    service, api = bridge
    data = await preview(service)
    root = await handoff(service, data, "root")
    service.creation.store.approve(
        root["proposal_id"], root["review_digest"], "Offline reviewer"
    )
    await service.creation._writer.aclose()
    service.creation._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/",
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    result = await service.creation.execute(7, root["proposal_id"])
    assert result["status"] == "uncertain"
    before = len(api.requests)
    server = create_server(
        service.notes.client, service.creation, service.editing, service.notes, service
    )
    response = await server.call_tool(
        "inspect_session_note_handoff",
        {
            "campaign_id": 7,
            "import_id": data["import_id"],
        },
    )
    assert not response.isError
    overview = response.structuredContent["data"]
    states = {op["operation_key"]: op for op in overview["operations"]}
    assert states["root"]["execution_state"] == "uncertain"
    assert states["child"]["blocked_by"] == ["root"]
    assert states["root-post"]["blocked_by"] == ["root"]
    assert {"existing-post", "journal"} <= set(overview["ready_operations"])
    assert not overview["execution_complete"]
    assert len(api.requests) == before
    assert (
        states["root"]["execution"]["diagnostics"][-1]["details"]["http_status"] == 500
    )
    rejected = await server.call_tool(
        "inspect_session_note_handoff",
        {
            "campaign_id": 7,
            "import_id": data["import_id"],
            "verify_result": True,
        },
    )
    assert rejected.isError
    assert "uncertain" in service.creation.status(7, root["proposal_id"])["progress"]


async def test_parent_child_and_post_bind_only_verified_universal_ids(bridge):
    service, api = bridge
    data = await preview(service)
    with pytest.raises(ValueError, match="no supervised preparation"):
        await handoff(service, data, "child")
    root = await handoff(service, data, "root")
    assert root["status"] == "pending" and root["verified_result"] is None
    with pytest.raises(KankaError, match="approval|Approval"):
        await service.creation.execute(7, root["proposal_id"])
    with pytest.raises(ValueError, match="not complete"):
        await handoff(service, data, "child")
    assert all(method == "GET" for method, _, _ in api.requests)
    created = await execute(service, root)
    checkpoint = len(api.requests)
    child = await handoff(service, data, "child")
    overview = service.inspect(7, data["import_id"])
    assert "root-post" in overview["ready_operations"]
    review = service.creation.store.review(child["proposal_id"])
    assert (
        review["preview"]["operations"][0]["body"]["parent_id"] == created["entity_id"]
    )
    assert created["entity_id"] != created["child_id"]
    assert child["status"] == "pending"
    post = await handoff(service, data, "root-post")
    assert (
        service.creation.store.draft(post["proposal_id"]).entity_id
        == created["entity_id"]
    )
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
    await execute(service, child)
    await execute(service, post)
    refreshed = await service.refresh(7, data["import_id"], "root-post")
    assert refreshed["verified_result"]["record_id"] > 0
    assert (await service.refresh(7, data["import_id"], "root"))["verified_result"][
        "entity_id"
    ] == created["entity_id"]


@pytest.mark.parametrize(
    "key", ["journal", "existing-post", "age", "post-edit", "inventory-edit"]
)
async def test_supported_preparations_need_exact_separate_approval(bridge, key):
    service, api = bridge
    data = await preview(service)
    link = await handoff(service, data, key)
    assert link["status"] == "pending"
    supervisor = service.creation if link["service"] == "creation" else service.editing
    with pytest.raises(KankaError, match="approval|Approval"):
        await supervisor.execute(7, link["proposal_id"])
    assert all(method == "GET" for method, _, _ in api.requests)
    await execute(service, link)
    checkpoint = len(api.requests)
    verified = await service.refresh(7, data["import_id"], key)
    assert verified["verified_result"]["proposal_id"] == link["proposal_id"]
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
    assert (await handoff(service, data, key))["status"] == "complete"


async def test_replays_do_not_reset_approval_or_terminal_state(bridge):
    service, api = bridge
    data = await preview(service)
    link = await handoff(service, data, "root")
    service.creation.store.approve(
        link["proposal_id"], link["review_digest"], "Offline reviewer"
    )
    assert (await handoff(service, data, "root"))["status"] == "approved"
    service.creation.store.revoke(link["proposal_id"])
    assert (await handoff(service, data, "root"))["status"] == "revoked"
    assert (
        service.creation.store._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[
            0
        ]
        == 1
    )
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize("after_persist", [False, True])
async def test_interrupted_preparation_recovers_same_id(
    bridge, monkeypatch, after_persist
):
    service, _api = bridge
    data = await preview(service)
    original = service.creation.prepare

    async def interrupted(*args, **kwargs):
        if after_persist:
            await original(*args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(service.creation, "prepare", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await handoff(service, data, "root")
    recorded = service.inspect(7, data["import_id"], "root")
    assert recorded["status"] == (
        "pending" if after_persist else "preparation_interrupted"
    )
    monkeypatch.setattr(service.creation, "prepare", original)
    recovered = await handoff(service, data, "root")
    assert recovered["proposal_id"] == recorded["proposal_id"]
    assert recovered["status"] == "pending"
    assert (
        service.creation.store._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[
            0
        ]
        == 1
    )


@pytest.mark.parametrize(
    "failure", ["version", "digest", "epoch", "key", "campaign", "canon"]
)
async def test_invalid_or_stale_handoff_creates_no_preparation(bridge, failure):
    service, api = bridge
    data = await preview(service)
    args = [7, data["import_id"], data["version"], data["preview_digest"], "age"]
    if failure == "version":
        args[2] -= 1
    if failure == "digest":
        args[3] = "changed"
    if failure == "epoch":
        service.notes.epoch = "restarted"
    if failure == "key":
        args[4] = "missing"
    if failure == "campaign":
        args[0] = 8
    if failure == "canon":
        api.records[101]["age"] = "30"
    with pytest.raises((ValueError, PermissionError)):
        await service.prepare(*args)
    assert (
        service.creation.store._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[
            0
        ]
        == 0
    )
    assert (
        service.editing.store._db.execute(
            "SELECT COUNT(*) FROM edit_proposals"
        ).fetchone()[0]
        == 0
    )
    assert all(method == "GET" for method, _, _ in api.requests)


async def test_changed_attachment_baseline_and_visibility_block_preparation(bridge):
    service, api = bridge
    data = await preview(service)
    api.attachments["posts"][0]["entry"] = "<p>Changed after the preview.</p>"
    with pytest.raises(ValueError, match="baseline changed"):
        await handoff(service, data, "post-edit")
    assert (
        service.editing.store._db.execute(
            "SELECT COUNT(*) FROM edit_proposals"
        ).fetchone()[0]
        == 0
    )


async def test_public_destination_and_contradictions_remain_blocked(bridge):
    service, api = bridge
    api.records[101]["is_private"] = False
    data = await preview(service)
    with pytest.raises(ValueError, match="blocked"):
        await handoff(service, data, "age")
    proposed = plan().model_dump(mode="json")
    proposed["assessments"] = [
        {"claim_key": "goal", "state": "contradiction", "explanation": "Needs review."}
    ]
    data = await preview(service, NotePlan.model_validate(proposed))
    with pytest.raises(ValueError, match="blocked"):
        await handoff(service, data, "root")


async def test_uncertain_parent_never_binds_or_resends(bridge):
    service, api = bridge
    data = await preview(service)
    link = await handoff(service, data, "root")
    api.lose_creation_response = True
    service.creation.store.approve(
        link["proposal_id"], link["review_digest"], "Offline reviewer"
    )
    result = await service.creation.execute(7, link["proposal_id"])
    assert result["status"] == "uncertain"
    checkpoint = len(api.requests)
    with pytest.raises(ValueError, match="not complete"):
        await handoff(service, data, "child")
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])


async def test_historical_mapping_survives_analysis_invalidation_and_restart(
    bridge, tmp_path
):
    service, _api = bridge
    data = await preview(service)
    link = await handoff(service, data, "root")
    await service.notes.submit(7, data["import_id"], data["version"], analysis())
    assert (
        service.inspect(7, data["import_id"], "root")["proposal_id"]
        == link["proposal_id"]
    )
    service.notes.store.close()
    service.notes.store = NoteStore(tmp_path / "notes.db")
    service.notes.epoch = "new-process"
    assert service.inspect(7, data["import_id"], "root")["status"] == "pending"


async def test_opt_in_mcp_contract_scoping_and_registration(bridge):
    service, _api = bridge
    notes, creator, editor = service.notes, service.creation, service.editing
    assert (
        len(await create_server(notes.client, creator, editor, notes).list_tools())
        == 38
    )
    server = create_server(notes.client, creator, editor, notes, service)
    tools = await server.list_tools()
    assert len(tools) == 40 and not any("approve" in t.name for t in tools)
    data = await preview(service)
    result = await server.call_tool(
        "prepare_session_note_handoff",
        {
            "campaign_id": 7,
            "import_id": data["import_id"],
            "expected_version": data["version"],
            "preview_digest": data["preview_digest"],
            "operation_key": "root",
        },
    )
    assert (
        not result.isError and result.structuredContent["data"]["status"] == "pending"
    )
    denied = await server.call_tool(
        "inspect_session_note_handoff",
        {"campaign_id": 8, "import_id": data["import_id"], "operation_key": "root"},
    )
    assert denied.isError and denied.structuredContent["data"] is None


async def test_concurrent_handoffs_create_one_immutable_proposal(bridge):
    service, _api = bridge
    data = await preview(service)
    left, right = await asyncio.gather(
        handoff(service, data, "root"), handoff(service, data, "root")
    )
    assert left["proposal_id"] == right["proposal_id"]
    assert (
        service.creation.store._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[
            0
        ]
        == 1
    )


@pytest.mark.parametrize("key", ["root", "age"])
async def test_source_provenance_is_part_of_terminal_review_digest(bridge, key):
    service, _api = bridge
    data = await preview(service)
    link = await handoff(service, data, key)
    supervisor = service.creation if key == "root" else service.editing
    review = supervisor.store.review(link["proposal_id"])
    provenance = review["provenance"] if key == "root" else review["plan"]["provenance"]
    assert provenance["import_id"] == data["import_id"]
    assert provenance["preview_digest"] == data["preview_digest"]
    assert provenance["source_refs"][0]["excerpt"]
    if key == "root":
        payload = json.loads(
            supervisor.store._db.execute(
                "SELECT payload FROM proposals WHERE id=?", (link["proposal_id"],)
            ).fetchone()[0]
        )
    else:
        payload = review["plan"]
    assert LocalApprovals._digest(payload) == link["review_digest"]
    payload["provenance"]["source_revision"] = "changed-source"
    assert LocalApprovals._digest(payload) != link["review_digest"]


async def test_revised_preview_cannot_replace_a_prepared_logical_operation(bridge):
    service, _api = bridge
    data = await preview(service)
    original = await handoff(service, data, "root")
    revised = plan().model_dump(mode="json")
    revised["operations"][0]["fields"]["name"] = "Revised root"
    data = await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(revised),
    )
    with pytest.raises(ValueError, match="different handoff"):
        await handoff(service, data, "root")
    assert (
        service.inspect(7, data["import_id"], "root")["proposal_id"]
        == original["proposal_id"]
    )


@pytest.mark.parametrize("legacy", [False, True])
async def test_different_posts_in_one_session_have_independent_handoffs(
    bridge, monkeypatch, legacy
):
    service, api = bridge
    first = next(op for op in plan().operations if op.key == "post-edit")
    second = first.model_copy(update={"key": "second-post-edit", "record_id": 62})
    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(
                service.notes,
                "_logical_key",
                lambda row, op, target: digest(
                    [row["document_id"], "s1", "edit_attachment", "post", 101, "admin"]
                ),
            )
        data = await preview(service, NotePlan(assessments=[], operations=[first]))
        original = await handoff(service, data, first.key)
    service.editing.store.approve(
        original["proposal_id"], original["review_digest"], "Offline reviewer"
    )
    approved = service.editing.store.review(original["proposal_id"])
    data = await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan(assessments=[], operations=[first, second]),
    )
    links = [await handoff(service, data, op.key) for op in (first, second)]
    assert links[0]["proposal_id"] == original["proposal_id"]
    assert service.editing.store.review(original["proposal_id"]) == approved
    assert links[0]["proposal_id"] != links[1]["proposal_id"]
    for op, link in zip((first, second), links, strict=True):
        review = service.editing.store.review(link["proposal_id"])
        assert review["plan"]["draft"]["record_id"] == op.record_id
        assert review["status"] == ("approved" if op is first else "pending")
    assert all(method == "GET" for method, _, _ in api.requests)


async def test_owner_privacy_race_is_rejected_before_edit_preparation(
    bridge, monkeypatch
):
    service, api = bridge
    data = await preview(service)
    original = service.editing.prepare

    async def changed(*args, **kwargs):
        api.records[101]["is_private"] = False
        return await original(*args, **kwargs)

    monkeypatch.setattr(service.editing, "prepare", changed)
    with pytest.raises(ValueError, match="baseline changed"):
        await handoff(service, data, "post-edit")
    assert (
        service.editing.store._db.execute(
            "SELECT COUNT(*) FROM edit_proposals"
        ).fetchone()[0]
        == 0
    )


async def test_scope_changes_and_created_identity_drift_block_dependencies(bridge):
    service, api = bridge
    data = await preview(service)
    link = await handoff(service, data, "root")
    created = await execute(service, link)
    api.records[created["entity_id"]]["id"] += 1
    with pytest.raises(ValueError, match="identity changed"):
        await handoff(service, data, "child")
    assert (
        service.creation.store._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[
            0
        ]
        == 1
    )
    service.creation.scope = CreationScope(
        campaign_ids=frozenset({7}), operations=frozenset({"journal.create"})
    )
    with pytest.raises(PermissionError):
        service.inspect(7, data["import_id"], "root")


@pytest.mark.parametrize("kind", ["creation", "editing"])
async def test_preparation_id_reuse_rejects_changed_content(bridge, kind):
    service, _api = bridge
    data = await preview(service)
    link = await handoff(service, data, "root" if kind == "creation" else "age")
    if kind == "creation":
        draft = service.creation.store.draft(link["proposal_id"]).model_copy(
            update={"name": "Different name"}
        )
        supervisor = service.creation
    else:
        from loremaster.edit_models import EditDraft

        draft = EditDraft.model_validate(
            service.editing.store.review(link["proposal_id"])["plan"]["draft"]
        ).model_copy(update={"changes": {"age": "46"}})
        supervisor = service.editing
    with pytest.raises(ValueError, match="different content"):
        await supervisor.prepare(draft, proposal_id=link["proposal_id"])


async def test_actual_stdio_handoff_local_approval_and_verified_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    authority = tmp_path / "supervised.db"
    notes = tmp_path / "notes.db"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "tests/lore_stdio_server.py")],
        cwd=str(root),
        env={
            "KANKA_API_TOKEN": "offline-handoff-stdio-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_ADMIN_CAMPAIGN_IDS": "7",
            "KANKA_CREATION_DATABASE": str(authority),
            "KANKA_WRITE_CAMPAIGN_IDS": "7",
            "KANKA_WRITE_OPERATIONS": "note.create,journal.create,post.create,"
            "character.update",
            "KANKA_NOTES_DATABASE": str(notes),
            "KANKA_NOTES_HANDOFF": "prepare",
        },
    )
    environment = {k: v for k, v in os.environ.items() if not k.startswith("KANKA_")}
    async with asyncio.timeout(45):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 40

                async def call(name, **args):
                    result = await session.call_tool(name, {"campaign_id": 7, **args})
                    assert not result.isError, result
                    return result.structuredContent["data"]

                imported = await call(
                    "import_session_notes", text=TEXT, context={"locale": "dmy"}
                )
                iid = imported["import_id"]
                analysed = await call(
                    "submit_session_note_analysis",
                    import_id=iid,
                    expected_version=0,
                    analysis=analysis().model_dump(mode="json"),
                )
                while analysed["work_cursor"]:
                    analysed = await call(
                        "submit_session_note_analysis",
                        import_id=iid,
                        expected_version=analysed["version"],
                        continuation=analysed["work_cursor"],
                    )
                planned = await call(
                    "prepare_session_note_proposals",
                    import_id=iid,
                    expected_version=analysed["version"],
                    evidence_digest=analysed["evidence_digest"],
                    plan=plan().model_dump(mode="json"),
                )
                created = {}
                for key in ("root", "child", "root-post"):
                    link = await call(
                        "prepare_session_note_handoff",
                        import_id=iid,
                        expected_version=planned["version"],
                        preview_digest=planned["preview_digest"],
                        operation_key=key,
                    )
                    assert link["status"] == "pending"
                    args = {"campaign_id": 7, "proposal_id": link["proposal_id"]}
                    assert (await session.call_tool("execute_creation", args)).isError
                    store = LocalApprovals(authority)
                    try:
                        review = store.review(link["proposal_id"])
                        assert review["provenance"]["import_id"] == iid
                        if key == "child":
                            assert (
                                store.draft(link["proposal_id"]).parent_entity_id
                                == created["root"]["entity_id"]
                            )
                    finally:
                        store.close()
                    approve_local(authority, review, environment)
                    result = await session.call_tool("execute_creation", args)
                    assert not result.isError, result
                    verified = await call(
                        "inspect_session_note_handoff",
                        import_id=iid,
                        operation_key=key,
                        verify_result=True,
                    )
                    created[key] = verified["verified_result"]
                    assert verified["status"] == "complete"
                assert created["root-post"]["entity_id"] == created["root"]["entity_id"]
    assert b"offline-handoff-stdio-token" not in authority.read_bytes()
    assert b"offline-handoff-stdio-token" not in notes.read_bytes()


@pytest.mark.parametrize(
    "case", ["unsupported_mode", "missing_notes", "missing_supervisor"]
)
def test_startup_requires_explicit_complete_handoff_configuration(
    tmp_path, monkeypatch, case
):
    from loremaster.server import main

    for name in list(os.environ):
        if name.startswith("KANKA_"):
            monkeypatch.delenv(name)
    values = {
        "KANKA_API_TOKEN": "offline-config-token",
        "KANKA_NOTES_HANDOFF": "prepare",
        "KANKA_NOTES_DATABASE": str(tmp_path / "notes.db"),
        "KANKA_CREATION_DATABASE": str(tmp_path / "supervised.db"),
        "KANKA_WRITE_CAMPAIGN_IDS": "7",
        "KANKA_WRITE_OPERATIONS": "note.create",
    }
    if case == "unsupported_mode":
        values["KANKA_NOTES_HANDOFF"] = "auto"
    if case == "missing_notes":
        values.pop("KANKA_NOTES_DATABASE")
    if case == "missing_supervisor":
        for name in (
            "KANKA_CREATION_DATABASE",
            "KANKA_WRITE_CAMPAIGN_IDS",
            "KANKA_WRITE_OPERATIONS",
        ):
            values.pop(name)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="HANDOFF=prepare requires"):
        main()
    assert not (tmp_path / "notes.db").exists()
    assert not (tmp_path / "supervised.db").exists()


@pytest.mark.parametrize("kind", ["creation", "editing"])
@pytest.mark.parametrize("invalid_id", ["", True, "unsafe/path"])
async def test_internal_preparation_id_boundaries_are_runtime_guards(
    bridge, kind, invalid_id
):
    service, _api = bridge
    data = await preview(service)
    link = await handoff(service, data, "root" if kind == "creation" else "age")
    if kind == "creation":
        store = service.creation.store
        draft = store.draft(link["proposal_id"])
        with pytest.raises(ValueError, match="execution_id"):
            store.prepare(
                draft,
                store.review(link["proposal_id"])["references"],
                proposal_id=invalid_id,
            )
    else:
        store = service.editing.store
        with pytest.raises(ValueError, match="execution_id"):
            store.prepare(
                store.review(link["proposal_id"])["plan"], proposal_id=invalid_id
            )


async def test_handoff_can_be_scoped_to_one_supervisor(bridge):
    service, _api = bridge
    notes = service.notes
    creator_only = SessionNoteBridge(notes, service.creation)
    editor_only = SessionNoteBridge(notes, editing=service.editing)
    assert (
        len(
            await create_server(
                notes.client, service.creation, notes=notes, note_bridge=creator_only
            ).list_tools()
        )
        == 27
    )
    assert (
        len(
            await create_server(
                notes.client,
                editing=service.editing,
                notes=notes,
                note_bridge=editor_only,
            ).list_tools()
        )
        == 27
    )
    data = await preview(service)
    with pytest.raises(PermissionError, match="not configured"):
        await handoff(creator_only, data, "age")
    with pytest.raises(PermissionError, match="not configured"):
        await handoff(editor_only, data, "root")
