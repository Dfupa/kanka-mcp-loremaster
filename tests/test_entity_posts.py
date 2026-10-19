"""Scoped post creation/editing on existing owners, never core-description edits."""

from copy import deepcopy

import httpx
import pytest
from test_lore_entities import approve
from test_lore_entities import services as services

from loremaster.creation import CreationScope, EntityPostDraft
from loremaster.edit_models import EditDraft
from loremaster.kanka import KankaError
from loremaster.server import create_server


def post(owner=602, name="Synthetic post"):
    return EntityPostDraft(
        campaign_id=7,
        entity_id=owner,
        name=name,
        entry="<p>Original test post.</p>",
        visibility_id=1,
    )


def enable(creator):
    creator.scope = CreationScope(
        campaign_ids=frozenset({7}), operations=frozenset({"post.create"})
    )


@pytest.mark.parametrize("owner", [101, 602, 604, 605])
async def test_post_create_and_edit_preserve_owner_and_siblings(services, owner):
    creator, editor, api = services
    enable(creator)
    before = deepcopy(api.records[owner])
    server = create_server(creator.reader, creator, editor)
    response = await server.call_tool(
        "prepare_entity_post_creation", {"draft": post(owner).model_dump(mode="json")}
    )
    assert not response.isError, response
    proposal = response.structuredContent["data"]
    assert (
        proposal["preview"]["operations"][0]["path"]
        == f"campaigns/7/entities/{owner}/posts"
    )
    approve(creator, proposal)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "verified" and result["entity_id"] == owner
    assert "child_id" not in result and result["post_id"] != owner
    first_id = result["post_id"]
    proposal = await creator.prepare(post(owner, "Untouched sibling"))
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    siblings = deepcopy(
        [row for row in api.post_records[owner] if row["id"] != first_id]
    )
    response = await server.call_tool(
        "prepare_post_edit",
        {
            "campaign_id": 7,
            "entity_id": owner,
            "post_id": first_id,
            "changes": {"entry": "Updated test post"},
        },
    )
    assert not response.isError, response
    proposal = response.structuredContent["data"]
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert [row for row in api.post_records[owner] if row["id"] != first_id] == siblings
    assert api.records[owner] == before


async def test_post_creation_has_separate_scope_and_owner_local_names(services):
    creator, _editor, api = services
    with pytest.raises(PermissionError):
        await creator.prepare(post())
    assert api.requests == []
    enable(creator)
    proposal = await creator.prepare(post(name=api.records[602]["name"]))
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    with pytest.raises(ValueError, match="exists"):
        await creator.prepare(post(name=api.records[602]["name"].upper()))
    assert await creator.prepare(post(605, api.records[602]["name"]))


async def test_lost_post_response_reconciles_exact_attachment_not_core_entity(services):
    creator, _editor, api = services
    enable(creator)
    proposal = await creator.prepare(post())
    api.lose_creation_response = True
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    with pytest.raises(PermissionError):
        approve(creator, proposal)
    candidate = api.post_records[602][0]["id"]
    checkpoint = len(api.requests)
    evidence = await creator.inspect_reconciliation(
        7, proposal["proposal_id"], candidate
    )
    assert (
        evidence["record"]["id"] == candidate and evidence["record"]["entity_id"] == 602
    )
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


async def test_wrong_post_owner_response_is_uncertain(services):
    creator, _editor, api = services
    enable(creator)
    proposal = await creator.prepare(post())

    def changed(request, response):
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"id": 2001, "entity_id": 605}})
        return response

    api.response_hook = changed
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "uncertain"


async def test_post_owner_id_cannot_be_a_module_id(services):
    creator, _editor, api = services
    enable(creator)
    with pytest.raises(KankaError):
        await creator.prepare(post(62))
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize(
    "owner,field,value",
    [(602, "instigator_id", 102), (604, "calendar_day", 4), (605, "is_pinned", 1)],
)
async def test_post_edits_guard_module_specific_owner_fields(
    services, owner, field, value
):
    creator, editor, api = services
    enable(creator)
    proposal = await creator.prepare(post(owner))
    approve(creator, proposal)
    created = await creator.execute(7, proposal["proposal_id"])
    proposal = await editor.prepare(
        EditDraft(
            campaign_id=7,
            entity_id=owner,
            kind="post",
            record_id=created["post_id"],
            changes={"entry": "Updated"},
        )
    )
    api.records[owner][field] = value
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "stale"
    assert not any(method == "PATCH" for method, _, _ in api.requests)
