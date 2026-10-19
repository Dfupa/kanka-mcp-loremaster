"""Rehearse approved creation, partial failures and read-back without networking."""

import asyncio

import httpx
import pytest
from creation_fixtures import approved, offline
from pydantic import ValidationError

from loremaster.kanka import KankaError


async def test_complete_creation_description_relation_inventory_and_two_posts(
    rehearsal,
):
    draft = offline.example_draft()
    proposal = offline.preview_creation(draft)
    assert proposal["mode"] == "proposal_only"
    assert [operation["key"] for operation in proposal["operations"]] == [
        "character",
        "relation:0",
        "inventory:0",
        "post:0",
        "post:1",
    ]
    assert all(operation["method"] == "POST" for operation in proposal["operations"])
    result = await rehearsal.run(approved(rehearsal, draft))
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
    first = await rehearsal.run(approved(rehearsal, draft))
    second = await rehearsal.run(approved(rehearsal, draft))
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
    result = await rehearsal.run(approved(rehearsal, draft))
    assert result["status"] == ("failed" if step == "character" else "partial")
    assert result["failed_step"] == step
    assert step not in result["completed"]
    assert "SYNTHETIC PRIVATE FAILURE" not in str(result)
    assert not any(method == "DELETE" for method, _ in rehearsal.requests)
    rehearsal.failure = None
    resumed = await rehearsal.run(approved(rehearsal, draft))
    assert resumed["status"] == "verified"
    assert len(rehearsal.records) == 3
    assert len(rehearsal.attachments[resumed["entity_id"]]["posts"]) == 2


@pytest.mark.parametrize("failure", ["timeout_after_commit", 500, 408, 302])
async def test_ambiguous_result_blocks_blind_retry(rehearsal, failure):
    draft = offline.example_draft()
    if failure == "timeout_after_commit":
        rehearsal.lose_response = "post:0"
    else:
        rehearsal.failure = ("post:0", failure)
    result = await rehearsal.run(approved(rehearsal, draft))
    assert result["status"] == "uncertain" and result["failed_step"] == "post:0"
    before = list(rehearsal.requests)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await rehearsal.run(approved(rehearsal, draft))
    assert rehearsal.requests == before
    if failure == "timeout_after_commit":
        assert len(rehearsal.attachments[result["entity_id"]]["posts"]) == 1


@pytest.mark.parametrize("change", ["approval", "content", "campaign"])
async def test_approval_binds_scope_and_exact_proposal_before_any_http(
    rehearsal, change
):
    draft = offline.example_draft()
    digest = offline.preview_creation(draft)["content_digest"]
    if change == "approval":
        digest = "unapproved"
    elif change == "content":
        draft = draft.model_copy(update={"entry": "Changed after approval"})
    else:
        draft = draft.model_copy(update={"campaign_id": 8})
    with pytest.raises(PermissionError):
        proposal = rehearsal.prepare(draft)
        grant = rehearsal.gate.approve(
            proposal["proposal_id"], reviewed_digest=digest, approved_by="Reviewer"
        )
        await rehearsal.run(grant)
    assert rehearsal.requests == []


async def test_content_checksum_is_not_an_execution_grant(rehearsal):
    draft = offline.example_draft()
    preview = rehearsal.prepare(draft)
    with pytest.raises(PermissionError, match="backend approval"):
        await rehearsal.run(preview["content_digest"])
    assert rehearsal.requests == []


async def test_same_grant_cannot_be_replayed(rehearsal):
    grant = approved(rehearsal, offline.example_draft())
    result = await rehearsal.run(grant)
    before = list(rehearsal.requests)
    with pytest.raises(PermissionError, match="consumed"):
        await rehearsal.run(grant)
    assert rehearsal.requests == before
    assert result["approval"]["approved_by"] == "offline-test-reviewer"
    assert grant.token not in str(result)


async def test_literal_placeholder_text_is_never_replaced_in_lore(rehearsal):
    draft = offline.example_draft().model_copy(
        update={
            "entry": "{entity_id}",
            "posts": (offline.PostDraft(name="{entity_id}", entry="{entity_id}"),),
        }
    )
    result = await rehearsal.run(approved(rehearsal, draft))
    assert result["bundle"]["details"]["entry"] == "{entity_id}"
    assert result["bundle"]["sections"]["posts"]["data"][0]["entry"] == "{entity_id}"


async def test_revocation_during_creation_stops_remaining_steps(rehearsal):
    draft = offline.example_draft()
    grant = approved(rehearsal, draft)
    original = rehearsal._handle

    def revoke_after_character(request):
        response = original(request)
        if request.method == "POST":
            rehearsal.gate.revoke(grant.proposal_id)
        return response

    await rehearsal._writer.aclose()
    rehearsal._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/",
        transport=httpx.MockTransport(revoke_after_character),
    )
    result = await rehearsal.run(grant)
    assert result["status"] == "partial"
    assert result["completed"] == ["character"]
    assert result["failed_step"] == "relation:0"
    assert result["error_code"] == "approval_denied"
    assert len([method for method, _ in rehearsal.requests if method == "POST"]) == 1


async def test_stale_partial_entity_blocks_resume_before_more_writes(rehearsal):
    draft = offline.example_draft()
    rehearsal.failure = ("post:1", 403)
    first = await rehearsal.run(approved(rehearsal, draft))
    rehearsal.failure = None
    rehearsal.records[first["entity_id"]]["entry"] = "Edited by another actor"
    count = len([method for method, _ in rehearsal.requests if method == "POST"])
    with pytest.raises(KankaError, match="differs from the approved"):
        await rehearsal.run(approved(rehearsal, draft))
    assert (
        len([method for method, _ in rehearsal.requests if method == "POST"]) == count
    )


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
        await rehearsal.run(approved(rehearsal, draft))
    assert not any(method == "POST" for method, _ in rehearsal.requests)
    missing = offline.example_draft().model_copy(
        update={
            "relations": (offline.RelationDraft(target_id=999, relation="Missing"),)
        }
    )
    with pytest.raises(KankaError) as error:
        await rehearsal.run(approved(rehearsal, missing))
    assert error.value.status_code == 404
    assert not any(method == "POST" for method, _ in rehearsal.requests)


async def test_duplicate_name_is_not_treated_as_permission_to_edit(rehearsal):
    draft = offline.example_draft().model_copy(update={"name": "Synthetic mentor"})
    with pytest.raises(ValueError, match="already exists"):
        await rehearsal.run(approved(rehearsal, draft))
    assert not any(method != "GET" for method, _ in rehearsal.requests)


async def test_read_back_detects_server_content_mismatch(rehearsal):
    draft = offline.example_draft()
    result = await rehearsal.run(approved(rehearsal, draft))
    rehearsal.attachments[result["entity_id"]]["posts"][1]["entry"] = (
        "Unexpected content"
    )
    with pytest.raises(KankaError, match="posts differ"):
        await rehearsal.run(approved(rehearsal, draft))
    assert len([method for method, _ in rehearsal.requests if method == "POST"]) == 5


@pytest.mark.parametrize("mode", ["json", "missing_data", "bad_id", "wrong_owner"])
async def test_unverifiable_success_blocks_retry_after_server_applies_step(
    rehearsal, mode
):
    draft = offline.example_draft()
    original = rehearsal._handle

    def malformed_success(request):
        response = original(request)
        if request.method == "POST" and rehearsal._active_key == "post:0":
            if mode == "json":
                return httpx.Response(200, text="PRIVATE MALFORMED BODY")
            if mode == "missing_data":
                return httpx.Response(200, json={})
            payload = response.json()
            field = "id" if mode == "bad_id" else "entity_id"
            payload["data"][field] = True if mode == "bad_id" else 999
            return httpx.Response(200, json=payload)
        return response

    await rehearsal._writer.aclose()
    rehearsal._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/",
        transport=httpx.MockTransport(malformed_success),
    )
    result = await rehearsal.run(approved(rehearsal, draft))
    assert result["status"] == "uncertain"
    assert result["error_code"] == "uncertain_result"
    assert len(rehearsal.attachments[result["entity_id"]]["posts"]) == 1
    assert "PRIVATE MALFORMED BODY" not in str(result)
    before = list(rehearsal.requests)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await rehearsal.run(approved(rehearsal, draft))
    assert rehearsal.requests == before


async def test_cancellation_after_send_marks_uncertainty_before_releasing_lock(
    rehearsal,
):
    draft = offline.example_draft()
    original = rehearsal._handle
    applied = asyncio.Event()

    async def interrupted(request):
        original(request)
        applied.set()
        await asyncio.Event().wait()

    await rehearsal._writer.aclose()
    rehearsal._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/",
        transport=httpx.MockTransport(interrupted),
    )
    task = asyncio.create_task(rehearsal.run(approved(rehearsal, draft)))
    async with asyncio.timeout(2):
        await applied.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(rehearsal.records) == 3
    before = list(rehearsal.requests)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await rehearsal.run(approved(rehearsal, draft))
    assert rehearsal.requests == before


async def test_concurrent_approved_retries_do_not_duplicate_steps(rehearsal):
    draft = offline.example_draft()
    results = await asyncio.gather(
        rehearsal.run(approved(rehearsal, draft)),
        rehearsal.run(approved(rehearsal, draft)),
    )
    assert all(result["status"] == "verified" for result in results)
    assert results[0]["entity_id"] == results[1]["entity_id"]
    assert len([method for method, _ in rehearsal.requests if method == "POST"]) == 5


async def test_changed_completed_post_stops_partial_resume_before_more_writes(
    rehearsal,
):
    draft = offline.example_draft()
    rehearsal.failure = ("post:1", 403)
    first = await rehearsal.run(approved(rehearsal, draft))
    rehearsal.failure = None
    rehearsal.attachments[first["entity_id"]]["posts"][0]["entry"] = "External change"
    count = len([method for method, _ in rehearsal.requests if method == "POST"])
    with pytest.raises(KankaError, match="posts differ"):
        await rehearsal.run(approved(rehearsal, draft))
    assert (
        len([method for method, _ in rehearsal.requests if method == "POST"]) == count
    )
