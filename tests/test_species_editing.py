"""Species description editing reuses guarded core PATCH, not character IDs."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_editing import approve, run
from test_editing import editor as editor

from loremaster.edit_models import EDIT_OPERATIONS, EditDraft
from loremaster.kanka import KankaError
from loremaster.server import create_server


def species(changes=None):
    return EditDraft(
        campaign_id=7,
        entity_id=201,
        kind="race",
        changes=changes
        or {"entry": "Raza común y extendida por todo el mundo sintético"},
    )


async def test_species_can_fill_explicitly_null_description(editor):
    service, api = editor
    api.records[201]["entry"] = None
    proposal, result = await run(service, species())
    assert proposal["plan"]["diff"]["entry"]["before"] == {
        "present": True,
        "value": None,
    }
    assert result["status"] == "verified"


@pytest.mark.parametrize("invalid", [False, 5, [], {}])
async def test_species_rejects_malformed_description_baseline(editor, invalid):
    service, api = editor
    api.records[201]["entry"] = invalid
    with pytest.raises(KankaError, match="description/privacy"):
        await service.prepare(species())
    assert all(method == "GET" for method, _, _ in api.requests)


async def test_species_rejects_missing_description_baseline(editor):
    service, api = editor
    api.records[201].pop("entry")
    with pytest.raises(KankaError, match="description/privacy"):
        await service.prepare(species())


async def test_species_description_preserves_identity_privacy_parent_and_references(
    editor,
):
    service, api = editor
    api.records[201].update(
        tags=[41], locations=[31], parent_id=22, type="Humanoid", updated_at="old"
    )
    before = deepcopy(api.records[201])
    _proposal, result = await run(service, species())
    assert result["status"] == "verified"
    assert api.records[201] == before | {
        "entry": species().changes["entry"],
        "updated_at": "version-2",
    }
    assert [row for row in api.requests if row[0] == "PATCH"] == [
        ("PATCH", "campaigns/7/races/21", species().changes)
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "New"},
        {"entry": None},
        {"entry": " "},
        {"entry": True},
        {"parent_id": 21},
        {"is_private": False},
    ],
)
def test_species_support_is_description_only(changes):
    with pytest.raises(ValidationError):
        species(changes)


async def test_species_requires_its_distinct_operation_before_http(editor):
    service, api = editor
    service.operations = EDIT_OPERATIONS - {"race.update"}
    with pytest.raises(PermissionError):
        await service.prepare(species())
    assert api.requests == []


async def test_species_rejects_character_and_module_ids(editor):
    service, api = editor
    for uid in (101, 21):
        with pytest.raises((ValueError, KankaError)):
            await service.prepare(species().model_copy(update={"entity_id": uid}))
    assert not any(method == "PATCH" for method, _, _ in api.requests)


@pytest.mark.parametrize(
    "field,value", [("parent_id", 22), ("entry", "Changed lore"), ("is_private", True)]
)
async def test_stale_species_never_sends_patch(editor, field, value):
    service, api = editor
    proposal = await service.prepare(species())
    approve(service, proposal)
    api.records[201][field] = value
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "stale"
    assert not any(method == "PATCH" for method, _, _ in api.requests)


async def test_species_mcp_preview_and_execution(editor):
    service, _api = editor
    server = create_server(service.reader, editing=service)
    prepared = await server.call_tool(
        "prepare_species_edit",
        {"campaign_id": 7, "entity_id": 201, "changes": species().changes},
    )
    assert not prepared.isError
    proposal = prepared.structuredContent["data"]
    assert proposal["plan"]["path"] == "campaigns/7/races/21"
    approve(service, proposal)
    result = await server.call_tool(
        "execute_edit", {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
    )
    assert (
        not result.isError and result.structuredContent["data"]["status"] == "verified"
    )
