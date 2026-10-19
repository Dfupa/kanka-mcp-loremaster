"""Rehearse approved creation, partial failures and read-back without networking."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from loremaster.kanka import KankaError

spec = spec_from_file_location(
    "offline_creation",
    Path(__file__).resolve().parents[1] / "scripts" / "offline_creation.py",
)
assert spec is not None and spec.loader is not None
offline = module_from_spec(spec)
spec.loader.exec_module(offline)


@pytest.fixture
async def rehearsal(monkeypatch):
    """Make any attempt to use a real HTTP transport fail the test."""
    network = AsyncMock(side_effect=AssertionError("Real networking is forbidden"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    simulation = offline.OfflineCreation()
    try:
        yield simulation
    finally:
        await simulation.aclose()
    network.assert_not_awaited()
    assert simulation._writer.is_closed
    assert simulation.reader._http.is_closed


def approved(draft):
    return offline.preview_creation(draft)["approval_digest"]


async def test_complete_creation_description_relation_inventory_and_two_posts(
    rehearsal,
):
    draft = offline.example_draft()
    proposal = offline.preview_creation(draft)
    assert proposal["mode"] == "offline_only"
    assert [operation["key"] for operation in proposal["operations"]] == [
        "character",
        "relation:0",
        "inventory:0",
        "post:0",
        "post:1",
    ]
    assert all(operation["method"] == "POST" for operation in proposal["operations"])
    result = await rehearsal.run(draft, proposal["approval_digest"])
    assert result["status"] == "verified"
    bundle = result["bundle"]
    entity_id = result["entity_id"]
    assert bundle["entity"]["id"] == entity_id
    assert bundle["details"]["id"] != entity_id
    assert bundle["details"]["name"] == "LoreMaster test — Élan"
    assert bundle["details"]["entry"] == draft.entry
    assert bundle["details"]["is_private"] is True
    relation = bundle["sections"]["relations"]["data"][0]
    assert relation["owner_id"] == entity_id and relation["target_id"] == 101
    assert relation["relation"] == "Mentor" and relation["direction"] == "outgoing"
    assert relation["visibility_id"] == 3 and relation["two_way"] is False
    item = bundle["sections"]["inventory"]["data"][0]
    assert item["item_id"] == 21 and item["item"]["entity_id"] == 201
    assert item["amount"] == "2" and item["is_equipped"] is True
    assert item["position"] == "Belt"
    posts = bundle["sections"]["posts"]["data"]
    assert [row["name"] for row in posts] == ["Test post one", "Test post two"]
    assert all(
        row["entity_id"] == entity_id and row["visibility_id"] == 3 for row in posts
    )
    assert [row["entry"] for row in posts] == [post.entry for post in draft.posts]
    assert len([method for method, _ in rehearsal.requests if method == "POST"]) == 5
    assert not any(
        method in ("DELETE", "PATCH", "PUT") for method, _ in rehearsal.requests
    )


async def test_repeating_approved_proposal_in_same_rehearsal_does_not_duplicate(
    rehearsal,
):
    draft = offline.example_draft()
    first = await rehearsal.run(draft, approved(draft))
    second = await rehearsal.run(draft, approved(draft))
    assert first["entity_id"] == second["entity_id"]
    assert second["status"] == "verified"
    assert len([method for method, _ in rehearsal.requests if method == "POST"]) == 5
    assert len(rehearsal.attachments[first["entity_id"]]["posts"]) == 2


@pytest.mark.parametrize(
    "step,status",
    [("character", 403), ("relation:0", 403), ("inventory:0", 422), ("post:1", 403)],
)
async def test_known_rejections_report_partial_progress_and_can_resume(
    rehearsal, step, status
):
    draft = offline.example_draft()
    rehearsal.failure = (step, status)
    result = await rehearsal.run(draft, approved(draft))
    assert result["status"] == ("failed" if step == "character" else "partial")
    assert result["failed_step"] == step
    assert step not in result["completed"]
    assert "SYNTHETIC PRIVATE FAILURE" not in str(result)
    assert not any(method == "DELETE" for method, _ in rehearsal.requests)
    rehearsal.failure = None
    resumed = await rehearsal.run(draft, approved(draft))
    assert resumed["status"] == "verified"
    assert len(rehearsal.records) == 3
    assert len(rehearsal.attachments[resumed["entity_id"]]["posts"]) == 2


@pytest.mark.parametrize("failure", ["timeout_after_commit", "server_error"])
async def test_ambiguous_result_blocks_blind_retry(rehearsal, failure):
    draft = offline.example_draft()
    if failure == "timeout_after_commit":
        rehearsal.lose_response = "post:0"
    else:
        rehearsal.failure = ("post:0", 500)
    result = await rehearsal.run(draft, approved(draft))
    assert result["status"] == "uncertain" and result["failed_step"] == "post:0"
    before = list(rehearsal.requests)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await rehearsal.run(draft, approved(draft))
    assert rehearsal.requests == before
    if failure == "timeout_after_commit":
        assert len(rehearsal.attachments[result["entity_id"]]["posts"]) == 1


@pytest.mark.parametrize("change", ["approval", "content", "campaign"])
async def test_approval_binds_scope_and_exact_proposal_before_any_http(
    rehearsal, change
):
    draft = offline.example_draft()
    digest = approved(draft)
    if change == "approval":
        digest = "unapproved"
    elif change == "content":
        draft = draft.model_copy(update={"entry": "Changed after approval"})
    else:
        draft = draft.model_copy(update={"campaign_id": 8})
        digest = approved(draft)
    with pytest.raises(PermissionError):
        await rehearsal.run(draft, digest)
    assert rehearsal.requests == []


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"campaign_id": True},
        {"is_private": "false"},
        {"delete": True},
        {"inventory": ({"item_entity_id": 201, "amount": 0},)},
        {"relations": ({"target_id": 101, "relation": "Mentor", "attitude": 101},)},
        {"posts": ({"name": "Post", "entry": "Text", "visibility_id": 9},)},
    ],
)
def test_invalid_drafts_cannot_be_previewed(changes):
    data = offline.example_draft().model_dump()
    with pytest.raises(ValidationError):
        offline.CharacterDraft.model_validate({**data, **changes})


async def test_invalid_or_wrong_type_references_stop_before_creation(rehearsal):
    draft = offline.example_draft().model_copy(
        update={"inventory": (offline.InventoryDraft(item_entity_id=101),)}
    )
    with pytest.raises(ValueError, match="item entities"):
        await rehearsal.run(draft, approved(draft))
    assert not any(method == "POST" for method, _ in rehearsal.requests)
    missing = offline.example_draft().model_copy(
        update={
            "relations": (offline.RelationDraft(target_id=999, relation="Missing"),)
        }
    )
    with pytest.raises(KankaError) as error:
        await rehearsal.run(missing, approved(missing))
    assert error.value.status_code == 404
    assert not any(method == "POST" for method, _ in rehearsal.requests)


async def test_duplicate_name_is_not_treated_as_permission_to_edit(rehearsal):
    draft = offline.example_draft().model_copy(update={"name": "Synthetic mentor"})
    with pytest.raises(ValueError, match="already exists"):
        await rehearsal.run(draft, approved(draft))
    assert not any(method != "GET" for method, _ in rehearsal.requests)


async def test_read_back_detects_server_content_mismatch(rehearsal):
    draft = offline.example_draft()
    result = await rehearsal.run(draft, approved(draft))
    rehearsal.attachments[result["entity_id"]]["posts"][1]["entry"] = (
        "Unexpected content"
    )
    with pytest.raises(KankaError, match="posts differ"):
        await rehearsal.run(draft, approved(draft))
    assert len([method for method, _ in rehearsal.requests if method == "POST"]) == 5
