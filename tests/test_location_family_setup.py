"""Location creation and real character-family assignment for dataset setup."""

import pytest
from pydantic import ValidationError
from test_lore_entities import approve
from test_lore_entities import services as services

from loremaster.creation import CreationScope, LocationDraft
from loremaster.edit_models import CharacterChanges, EditDraft
from loremaster.server import create_server


async def test_location_creation_and_character_family_assignment(services):
    creator, editor, api = services
    creator.scope = CreationScope(
        campaign_ids=frozenset({7}), operations=frozenset({"location.create"})
    )
    server = create_server(creator.reader, creator, editor)
    draft = LocationDraft(
        campaign_id=7,
        name="Synthetic harbour",
        entry="<p>Test location.</p>",
        title="Harbour",
        tag_entity_ids=(401,),
        is_private=False,
    )
    response = await server.call_tool(
        "prepare_location_creation", {"draft": draft.model_dump(mode="json")}
    )
    assert not response.isError
    proposal = response.structuredContent["data"]
    approve(creator, proposal)
    created = await creator.execute(7, proposal["proposal_id"])
    assert created["status"] == "verified"
    assert created["entity_id"] != created["child_id"]
    assert api.records[created["entity_id"]]["title"] == "Harbour"
    assert [
        (method, path, body) for method, path, body in api.requests if method == "POST"
    ] == [
        (
            "POST",
            "campaigns/7/locations",
            {
                "name": draft.name,
                "entry": draft.entry,
                "is_private": False,
                "title": "Harbour",
                "tags": [41],
            },
        )
    ]
    api.records[101].update(families=[], private_families=[800])
    proposal = await editor.prepare(
        EditDraft(
            campaign_id=7,
            entity_id=101,
            kind="character",
            changes={"family_entity_ids": [601]},
        )
    )
    assert proposal["plan"]["body"] == {"families": [61]}
    approve(editor, proposal)
    assert (await editor.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert api.records[101]["families"] == [61]
    assert api.records[101]["private_families"] == [800]


async def test_location_creation_requires_its_own_scope(services):
    creator, _editor, api = services
    with pytest.raises(PermissionError):
        await creator.prepare(
            LocationDraft(campaign_id=7, name="Harbour", entry="Description")
        )
    assert api.requests == []


async def test_character_family_reference_must_be_family(services):
    _creator, editor, api = services
    api.records[101]["families"] = []
    with pytest.raises(ValueError, match="family"):
        await editor.prepare(
            EditDraft(
                campaign_id=7,
                entity_id=101,
                kind="character",
                changes={"family_entity_ids": [301]},
            )
        )
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize("value", [None, [True], ["601"], [601, 601]])
def test_character_family_references_are_strict(value):
    with pytest.raises(ValidationError):
        CharacterChanges.model_validate({"family_entity_ids": value})
