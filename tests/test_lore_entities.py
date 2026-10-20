"""Offline create/edit coverage for families, missions, maps, diaries and notes."""

from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from lore_fixture import KINDS, TARGETS, LoreAPI
from pydantic import ValidationError

from loremaster.approvals import LocalApprovals
from loremaster.creation import DRAFT_MODELS, CreationScope, preview_creation
from loremaster.edit_models import CHANGE_MODELS, EDIT_OPERATIONS, EditDraft
from loremaster.edit_store import EditStore
from loremaster.editing import EditService
from loremaster.execution import CreationService
from loremaster.kanka import KankaClient, KankaError
from loremaster.server import create_server


def new_draft(kind):
    fields = {
        "campaign_id": 7,
        "name": "Synthetic Élan " + kind,
        "entry": "<p>Original synthetic lore.</p>",
        "type": "Test",
        "tag_entity_ids": (401,),
    }
    if kind in ("family", "quest", "journal"):
        fields["location_entity_ids"] = (301,)
    if kind == "quest":
        fields["instigator_entity_id"] = 102
    elif kind == "map":
        fields.update(location_entity_id=301, is_real=False)
    elif kind == "journal":
        fields.update(author_entity_id=101, date="Year 12, Frostfall 3")
    return DRAFT_MODELS[kind](**fields)


def changes(kind):
    fields = {
        "name": "Revised Élan " + kind,
        "entry": "<p>Revised lore.</p>",
        "type": "Revised",
        "tag_entity_ids": [402],
    }
    if kind in ("family", "quest", "journal"):
        fields["location_entity_ids"] = [302]
    if kind == "quest":
        fields["instigator_entity_id"] = 101
    elif kind == "map":
        fields.update(location_entity_id=302, is_real=True)
    elif kind == "journal":
        fields.update(author_entity_id=102, date="Year 12, Frostfall 4")
    return fields


@pytest.fixture
async def services(tmp_path, monkeypatch):
    network = AsyncMock(side_effect=AssertionError("Real networking forbidden"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    api = LoreAPI()
    reader = KankaClient(
        "offline-lore-token",
        allowed_campaign_ids=frozenset({7}),
        admin_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(api.handle),
    )
    database = tmp_path / "private.sqlite3"
    creator = CreationService(
        reader,
        "offline-lore-token",
        LocalApprovals(database),
        CreationScope(
            campaign_ids=frozenset({7}),
            operations=frozenset(kind + ".create" for kind in KINDS),
        ),
        transport=httpx.MockTransport(api.handle),
    )
    editor = EditService(
        reader,
        "offline-lore-token",
        EditStore(database),
        frozenset({7}),
        EDIT_OPERATIONS,
        transport=httpx.MockTransport(api.handle),
    )
    try:
        yield creator, editor, api
    finally:
        await editor.aclose()
        await creator.aclose()
        await reader.aclose()
    network.assert_not_awaited()


def approve(service, proposal):
    service.store.approve(
        proposal["proposal_id"], proposal["review_digest"], "Offline reviewer"
    )


@pytest.mark.parametrize("kind", KINDS)
async def test_mcp_create_then_edit_each_new_type(services, kind):
    creator, editor, api = services
    server = create_server(creator.reader, creator, editor)
    draft = new_draft(kind)
    result = await server.call_tool(
        "prepare_" + kind + "_creation", {"draft": draft.model_dump(mode="json")}
    )
    assert not result.isError, result
    proposal = result.structuredContent["data"]
    assert all(method == "GET" for method, _, _ in api.requests)
    operation = proposal["preview"]["operations"][0]
    assert operation["path"] == "campaigns/7/" + KINDS[kind]
    assert operation["body"]["is_private"] is True
    args = {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
    assert (await server.call_tool("execute_creation", args)).isError
    approve(creator, proposal)
    result = await server.call_tool("execute_creation", args)
    assert not result.isError, result
    created = result.structuredContent["data"]
    uid, child = created["entity_id"], created["child_id"]
    assert created["status"] == "verified" and uid != child
    assert api.records[uid]["tags"] == [41]
    assert (await server.call_tool("execute_creation", args)).isError
    editing = await server.call_tool(
        "prepare_" + kind + "_edit",
        {"campaign_id": 7, "entity_id": uid, "changes": changes(kind)},
    )
    assert not editing.isError, editing
    proposal = editing.structuredContent["data"]
    args["proposal_id"] = proposal["proposal_id"]
    assert (await server.call_tool("execute_edit", args)).isError
    approve(editor, proposal)
    result = await server.call_tool("execute_edit", args)
    assert not result.isError, result
    assert result.structuredContent["data"]["status"] == "verified"
    assert api.records[uid]["name"] == changes(kind)["name"]
    assert api.records[uid]["tags"] == [42]
    assert api.records[uid]["is_private"] is True
    if kind in ("family", "quest", "journal"):
        assert api.records[uid]["locations"] == [32]
    if kind == "map":
        assert api.records[uid]["location_id"] == 32
    if kind == "journal":
        assert api.records[uid]["author_id"] == 102
    if kind == "quest":
        assert api.records[uid]["instigator_id"] == 101
    assert (await server.call_tool("execute_edit", args)).isError
    assert [
        (method, path)
        for method, path, _ in api.requests
        if method in ("POST", "PATCH")
    ] == [
        ("POST", "campaigns/7/" + KINDS[kind]),
        ("PATCH", f"campaigns/7/{KINDS[kind]}/{child}"),
    ]


@pytest.mark.parametrize("kind", KINDS)
async def test_new_types_require_their_own_create_and_update_scopes(services, kind):
    creator, editor, api = services
    creator.scope = CreationScope(
        campaign_ids=frozenset({7}), operations=frozenset({"character.create"})
    )
    with pytest.raises(PermissionError):
        await creator.prepare(new_draft(kind))
    editor.operations = frozenset({"character.update"})
    with pytest.raises(PermissionError):
        await editor.prepare(
            EditDraft(
                campaign_id=7,
                entity_id=TARGETS[kind][0],
                kind=kind,
                changes={"entry": "New"},
            )
        )
    assert api.requests == []


@pytest.mark.parametrize("kind", KINDS)
async def test_edit_preserves_module_metadata_and_rejects_stale_state(services, kind):
    _creator, editor, api = services
    uid, _child = TARGETS[kind]
    before = deepcopy(api.records[uid])
    edit = EditDraft(
        campaign_id=7, entity_id=uid, kind=kind, changes={"entry": "<p>New.</p>"}
    )
    proposal = await editor.prepare(edit)
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert api.records[uid] == before | {
        "entry": "<p>New.</p>",
        "updated_at": "version-2",
    }
    proposal = await editor.prepare(
        edit.model_copy(update={"changes": {"type": "Other"}})
    )
    api.records[uid]["parent_id"] = 901
    checkpoint = len(api.requests)
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "stale"
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])


@pytest.mark.parametrize("kind", KINDS)
async def test_unknown_creation_outcome_requires_readonly_reconciliation(
    services, kind
):
    creator, _editor, api = services
    draft = new_draft(kind)
    proposal = await creator.prepare(draft)
    approve(creator, proposal)
    api.lose_creation_response = True
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    with pytest.raises(PermissionError):
        approve(creator, proposal)
    uid = next(uid for uid, row in api.records.items() if row["name"] == draft.name)
    checkpoint = len(api.requests)
    evidence = await creator.inspect_reconciliation(7, proposal["proposal_id"], uid)
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
    creator.store.journal.confirm_reconciled(
        proposal["proposal_id"],
        evidence["fingerprint"],
        evidence["record"],
        reviewed_by="Origin reviewer",
    )
    api.lose_creation_response = False
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert sum(method == "POST" for method, _, _ in api.requests) == 1


@pytest.mark.parametrize("kind", KINDS)
async def test_reference_type_and_wrong_owner_type_fail_closed(services, kind):
    creator, editor, api = services
    bad = new_draft(kind).model_copy(update={"tag_entity_ids": (301,)})
    with pytest.raises(ValueError, match="tag"):
        await creator.prepare(bad)
    with pytest.raises(ValueError, match=kind):
        await editor.prepare(
            EditDraft(campaign_id=7, entity_id=101, kind=kind, changes={"entry": "New"})
        )
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "field,value",
    [
        ("name", " "),
        ("entry", None),
        ("is_private", "false"),
        ("tag_entity_ids", (401, 401)),
        ("parent_id", 601),
        ("image", "/tmp/image.png"),
    ],
)
def test_creation_contracts_reject_unsupported_or_malformed_values(kind, field, value):
    with pytest.raises(ValidationError):
        DRAFT_MODELS[kind].model_validate(new_draft(kind).model_dump() | {field: value})


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "values",
    [
        {"entry": None},
        {"is_private": False},
        {"name": " "},
        {"tag_entity_ids": [True]},
        {"tag_entity_ids": [401, 401]},
        {"parent_id": 601},
    ],
)
def test_edits_reject_invalid_or_out_of_scope_fields(kind, values):
    with pytest.raises(ValidationError):
        CHANGE_MODELS[kind].model_validate(values)


@pytest.mark.parametrize("kind", KINDS)
def test_new_types_have_private_minimal_creation_previews(kind):
    draft = DRAFT_MODELS[kind](campaign_id=7, name="Élan " + kind, entry="Description")
    op = preview_creation(draft)["operations"][0]
    assert op["path"] == "campaigns/7/" + KINDS[kind]
    assert op["operation"] == kind + ".create"
    assert op["body"] == {
        "name": draft.name,
        "entry": draft.entry,
        "is_private": True,
    } | ({"is_real": False} if kind == "map" else {})


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("family", "members", ["12"]),
        ("quest", "elements", []),
        ("map", "layers", []),
        ("journal", "calendar_day", 4),
        ("note", "is_pinned", 1),
    ],
)
async def test_untouched_module_changes_after_patch_cannot_pass_verification(
    services, kind, field, value
):
    _creator, editor, api = services
    uid, _child = TARGETS[kind]
    proposal = await editor.prepare(
        EditDraft(campaign_id=7, entity_id=uid, kind=kind, changes={"entry": "Updated"})
    )

    def changed(request, response):
        if request.method == "PATCH":
            api.records[uid][field] = value
        return response

    api.response_hook = changed
    approve(editor, proposal)
    result = await editor.execute(7, proposal["proposal_id"])
    assert result["status"] == "partial" and result["outcome"] == "confirmed"
    checkpoint = len(api.requests)
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "partial"
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])


@pytest.mark.parametrize("kind", KINDS)
async def test_lost_patch_blocks_new_sends_and_requires_reviewed_origin(services, kind):
    _creator, editor, api = services
    uid, _child = TARGETS[kind]
    draft = EditDraft(
        campaign_id=7, entity_id=uid, kind=kind, changes={"entry": "Updated"}
    )
    proposal = await editor.prepare(draft)
    api.lose_response = True
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    with pytest.raises(PermissionError):
        approve(editor, proposal)
    with pytest.raises(PermissionError):
        await editor.prepare(
            draft.model_copy(update={"changes": {"entry": "Another change"}})
        )
    checkpoint = len(api.requests)
    evidence = await editor.inspect_reconciliation(7, proposal["proposal_id"])
    assert evidence["matches_requested_result"] and not evidence["origin_verified"]
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
    editor.store.confirm_reconciled(
        proposal["proposal_id"], evidence["fingerprint"], "Origin reviewer"
    )
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert sum(method == "PATCH" for method, _, _ in api.requests) == 1


@pytest.mark.parametrize("kind", KINDS)
async def test_rejected_creation_needs_new_approval(services, kind):
    creator, _editor, api = services
    proposal = await creator.prepare(new_draft(kind))
    api.creation_failure = 403
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "failed"
    assert creator.store.journal.states(proposal["proposal_id"]) == {kind: "rejected"}
    with pytest.raises(KankaError, match="approval"):
        await creator.execute(7, proposal["proposal_id"])
    api.creation_failure = None
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"


@pytest.mark.parametrize("kind", KINDS)
async def test_cannot_rename_to_duplicate_or_edit_using_module_id(services, kind):
    _creator, editor, api = services
    uid, child = TARGETS[kind]
    with pytest.raises(ValueError, match="conflict"):
        await editor.prepare(
            EditDraft(
                campaign_id=7, entity_id=uid, kind=kind, changes={"name": "Mentor"}
            )
        )
    with pytest.raises(KankaError):
        await editor.prepare(
            EditDraft(
                campaign_id=7, entity_id=child, kind=kind, changes={"entry": "Updated"}
            )
        )
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize("kind", KINDS)
async def test_clear_tag_assignments_is_explicit_and_noops_are_rejected(services, kind):
    _creator, editor, api = services
    uid, _child = TARGETS[kind]
    draft = EditDraft(
        campaign_id=7, entity_id=uid, kind=kind, changes={"tag_entity_ids": []}
    )
    proposal = await editor.prepare(draft)
    assert proposal["plan"]["body"] == {"tags": []}
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert api.records[uid]["tags"] == []
    with pytest.raises(ValueError, match="no changes"):
        await editor.prepare(draft)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", [[True], ["401"], "401", {"id": 401}])
def test_mcp_array_freezing_never_coerces_invalid_ids(kind, value):
    with pytest.raises(ValidationError):
        DRAFT_MODELS[kind].model_validate(
            new_draft(kind).model_dump() | {"tag_entity_ids": value}
        )
