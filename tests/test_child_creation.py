"""Child quests and notes bind verified universal parent IDs, not module IDs."""

import httpx
import pytest
from pydantic import ValidationError
from test_lore_entities import approve
from test_lore_entities import services as services

from loremaster.creation import DRAFT_MODELS, preview_creation
from loremaster.kanka import KankaError


@pytest.mark.parametrize("kind,parent", [("quest", 602), ("note", 605)])
async def test_child_creation_preserves_universal_parent_in_review_and_readback(
    services, kind, parent
):
    creator, _editor, api = services
    draft = DRAFT_MODELS[kind](
        campaign_id=7,
        name="Synthetic child " + kind,
        entry="<p>Child.</p>",
        parent_entity_id=parent,
    )
    preview = preview_creation(draft)
    assert preview["operations"][0]["body"]["parent_id"] == parent
    proposal = await creator.prepare(draft)
    assert proposal["references"]["entities"][str(parent)]["entity_type"] == kind
    approve(creator, proposal)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "verified"
    assert api.records[result["entity_id"]]["parent_id"] == parent
    assert parent != api.records[parent]["id"]


@pytest.mark.parametrize("kind", ["quest", "note"])
async def test_child_parent_must_have_same_type_and_visible_universal_id(
    services, kind
):
    creator, _editor, api = services
    for parent in (301, 101):
        draft = DRAFT_MODELS[kind](
            campaign_id=7, name="Child", entry="Description", parent_entity_id=parent
        )
        with pytest.raises(ValueError, match=kind):
            await creator.prepare(draft)
    draft = DRAFT_MODELS[kind](
        campaign_id=7, name="Child", entry="Description", parent_entity_id=62
    )
    with pytest.raises(KankaError):
        await creator.prepare(draft)
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize("kind,parent", [("quest", 602), ("note", 605)])
async def test_changed_parent_identity_blocks_child_creation(services, kind, parent):
    creator, _editor, api = services
    proposal = await creator.prepare(
        DRAFT_MODELS[kind](
            campaign_id=7, name="Child", entry="Description", parent_entity_id=parent
        )
    )
    api.records[parent]["name"] = "Changed parent"
    approve(creator, proposal)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "failed"
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize("kind", ["quest", "note"])
@pytest.mark.parametrize("parent", [True, "602", 0, -1])
def test_child_parent_id_is_strict(kind, parent):
    with pytest.raises(ValidationError):
        DRAFT_MODELS[kind](
            campaign_id=7, name="Child", entry="Description", parent_entity_id=parent
        )


@pytest.mark.parametrize("kind,parent", [("quest", 602), ("note", 605)])
async def test_live_parent_metadata_only_shape_and_confirmed_readonly_retry(
    services, kind, parent
):
    creator, _editor, api = services
    wrong = {"value": True}

    def hook(request, response):
        if request.method != "GET" or not isinstance(response.json().get("data"), dict):
            return response
        payload = response.json()
        row = payload["data"]
        if row.get("name") != "Child":
            return response
        if "/entities/" in request.url.path:
            if wrong["value"]:
                row["parent_id"] = None
        else:
            row.pop("parent_id", None)
        return httpx.Response(200, json=payload)

    api.response_hook = hook
    draft = DRAFT_MODELS[kind](
        campaign_id=7, name="Child", entry="Description", parent_entity_id=parent
    )
    proposal = await creator.prepare(draft)
    approve(creator, proposal)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "partial"
    assert creator.store.journal.states(proposal["proposal_id"]) == {kind: "confirmed"}
    wrong["value"] = False
    checkpoint = len(api.requests)
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
    assert sum(method == "POST" for method, _, _ in api.requests) == 1


@pytest.mark.parametrize("observed", [None, True, 62, "602"])
async def test_generic_parent_identity_cannot_be_replaced_by_module_field(
    services, observed
):
    creator, _editor, api = services

    def hook(request, response):
        if request.method == "GET" and "/entities/" in request.url.path:
            payload = response.json()
            if payload["data"].get("name") == "Child":
                payload["data"]["parent_id"] = observed
                return httpx.Response(200, json=payload)
        return response

    api.response_hook = hook
    proposal = await creator.prepare(
        DRAFT_MODELS["quest"](
            campaign_id=7, name="Child", entry="Description", parent_entity_id=602
        )
    )
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "partial"


async def test_conflicting_generic_and_module_parent_fields_fail_closed(services):
    creator, _editor, api = services

    def hook(request, response):
        if request.method == "GET" and "/quests/" in request.url.path:
            payload = response.json()
            if payload["data"].get("name") == "Child":
                payload["data"]["parent_id"] = 999
                return httpx.Response(200, json=payload)
        return response

    api.response_hook = hook
    proposal = await creator.prepare(
        DRAFT_MODELS["quest"](
            campaign_id=7, name="Child", entry="Description", parent_entity_id=602
        )
    )
    approve(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "partial"


async def test_reconciliation_reads_parent_from_generic_metadata(services):
    creator, _editor, api = services

    def hook(request, response):
        if request.method == "GET" and "/notes/" in request.url.path:
            payload = response.json()
            payload["data"].pop("parent_id", None)
            return httpx.Response(200, json=payload)
        return response

    api.response_hook = hook
    proposal = await creator.prepare(
        DRAFT_MODELS["note"](
            campaign_id=7, name="Child", entry="Description", parent_entity_id=605
        )
    )
    approve(creator, proposal)
    api.lose_creation_response = True
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    uid = next(uid for uid, row in api.records.items() if row["name"] == "Child")
    evidence = await creator.inspect_reconciliation(7, proposal["proposal_id"], uid)
    assert evidence["record"]["parent_id"] == 605
