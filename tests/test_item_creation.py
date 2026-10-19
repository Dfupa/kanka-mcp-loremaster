"""Supervised item records are distinct from character inventory attachments."""

import httpx
import pytest
from pydantic import ValidationError
from supervised_fixture import handle
from test_supervised_execution import authorize
from test_supervised_execution import service as service

from loremaster.creation import CreationScope, ItemDraft, preview_creation
from loremaster.server import create_server


@pytest.fixture
def item(service):
    creator, _api = service
    creator.scope = CreationScope(
        campaign_ids=frozenset({7}),
        operations=creator.scope.operations | {"item.create"},
    )
    return ItemDraft(
        campaign_id=7,
        name="New synthetic compass",
        entry="<p>A synthetic compass.</p>",
        type="Tool",
        price="1 gp",
        size="Small",
        weight="0.1 kg",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", " "),
        ("entry", ""),
        ("price", 1),
        ("weight", 0.1),
        ("type", True),
        ("size", " "),
        ("is_private", "false"),
    ],
)
def test_item_fields_reject_coercion_and_empty_text(field, value):
    with pytest.raises(ValidationError):
        ItemDraft.model_validate(
            {"campaign_id": 7, "name": "Compass", "entry": "Description", field: value}
        )


def test_item_preview_binds_only_supported_fields_and_private_default():
    draft = ItemDraft(campaign_id=7, name="Compass", entry="Description")
    preview = preview_creation(draft)
    assert preview["operations"] == [
        {
            "key": "item",
            "operation": "item.create",
            "method": "POST",
            "path": "campaigns/7/items",
            "body": {"name": "Compass", "entry": "Description", "is_private": True},
        }
    ]
    with pytest.raises(ValidationError):
        ItemDraft.model_validate({**draft.model_dump(), "location_id": 31})
    changed = draft.model_copy(update={"price": "2 gp"})
    assert preview["content_digest"] != preview_creation(changed)["content_digest"]


async def test_mcp_item_creation_uses_its_own_operation_class(service, item):
    creator, api = service
    server = create_server(creator.reader, creator)
    prepared = await server.call_tool(
        "prepare_item_creation", item.model_dump(mode="json")
    )
    assert not prepared.isError
    proposal = prepared.structuredContent["data"]
    assert isinstance(creator.store.draft(proposal["proposal_id"]), ItemDraft)
    assert all(method == "GET" for method, _path in api.requests)
    authorize(creator, proposal)
    args = {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
    wrong = await server.call_tool("execute_character_creation", args)
    assert (
        wrong.isError
        and creator.store.review(proposal["proposal_id"])["status"] == "approved"
    )
    committed = await server.call_tool("execute_creation", args)
    assert not committed.isError
    result = committed.structuredContent["data"]
    assert result["status"] == "verified" and result["entity_id"] != result["child_id"]
    stored = api.records[result["entity_id"]]
    assert {
        key: stored[key]
        for key in ("name", "entry", "is_private", "type", "price", "size", "weight")
    } == item.model_dump(exclude={"campaign_id"})
    assert [(method, path) for method, path in api.requests if method == "POST"] == [
        ("POST", "campaigns/7/items")
    ]
    assert not api.attachments.get(result["entity_id"])
    assert creator.status(7, proposal["proposal_id"])["status"] == "complete"


async def test_item_scope_is_required_before_any_http(service, item):
    creator, api = service
    creator.scope = CreationScope(
        campaign_ids=frozenset({7}),
        operations=frozenset({"character.create", "inventory.add"}),
    )
    with pytest.raises(PermissionError, match="scope"):
        await creator.prepare(item)
    assert api.requests == []


async def test_rejected_item_requires_fresh_approval(service, item):
    creator, api = service
    proposal = await creator.prepare(item)
    authorize(creator, proposal)
    api.failure = ("item", 403)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "failed" and result["completed"] == []
    assert creator.store.journal.states(proposal["proposal_id"]) == {"item": "rejected"}
    denied = await create_server(creator.reader, creator).call_tool(
        "execute_creation", {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
    )
    assert denied.isError
    api.failure = None
    authorize(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert sum(method == "POST" for method, _path in api.requests) == 2


async def test_lost_item_response_requires_candidate_readback_and_fresh_approval(
    service, item
):
    creator, api = service
    proposal = await creator.prepare(item)
    authorize(creator, proposal)
    api.lose_response = "item"
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    with pytest.raises(PermissionError, match="Reconcile uncertain"):
        authorize(creator, proposal)
    candidate = next(
        uid for uid, row in api.records.items() if row["name"] == item.name
    )
    before = len(api.requests)
    evidence = await creator.inspect_reconciliation(
        7, proposal["proposal_id"], candidate
    )
    assert all(method == "GET" for method, _path in api.requests[before:])
    creator.store.journal.confirm_reconciled(
        proposal["proposal_id"],
        evidence["fingerprint"],
        evidence["record"],
        reviewed_by="Offline reviewer",
    )
    api.lose_response = None
    authorize(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert sum(method == "POST" for method, _path in api.requests) == 1


@pytest.mark.parametrize(
    "field,value", [("price", "2 gp"), ("weight", "2 kg"), ("is_private", False)]
)
async def test_item_readback_mismatch_is_never_success(service, item, field, value):
    creator, api = service
    proposal = await creator.prepare(item)
    authorize(creator, proposal)

    def changed(request):
        response = handle(api, request)
        if request.method == "POST":
            next(row for row in api.records.values() if row["name"] == item.name)[
                field
            ] = value
        return response

    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(changed)
    )
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "partial" and result["completed"] == ["item"]
    assert result["failed_step"] == "verification"


async def test_core_journal_ignores_unrelated_nullable_membership_fields(service, item):
    creator, api = service
    proposal = await creator.prepare(item)
    assert proposal["entity_type"] == "item"
    authorize(creator, proposal)

    def handler(request):
        response = handle(api, request)
        if request.method == "POST":
            payload = response.json()
            payload["data"].update(organisation_id=None, character_id=None)
            return httpx.Response(201, json=payload)
        return response

    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(handler)
    )
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "verified" and result["entity_type"] == "item"
    progress = creator.store.journal.progress(proposal["proposal_id"])["item"]
    assert set(progress) == {"id", "entity_id"}
