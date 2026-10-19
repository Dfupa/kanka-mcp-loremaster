"""Offline edit safeguards and all five requested resource contracts."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import AsyncMock

import httpx
import pytest
from edit_fixture import EditAPI
from pydantic import ValidationError

from loremaster.approvals import LocalApprovals
from loremaster.creation import CharacterDraft
from loremaster.edit_models import EDIT_OPERATIONS, EditDraft
from loremaster.edit_store import EditStore
from loremaster.editing import EditService, positive
from loremaster.kanka import KankaClient, KankaError
from loremaster.server import create_server


def draft(kind="character", changes=None, **kwargs):
    return EditDraft(
        campaign_id=7,
        entity_id=101,
        kind=kind,
        record_id={
            "character": None,
            "post": 61,
            "inventory": 71,
            "relation": 81,
            "tag": 91,
        }[kind],
        changes=changes if changes is not None else {"entry": "<p>New lore.</p>"},
        **kwargs,
    )


@pytest.fixture
async def editor(tmp_path, monkeypatch):
    network = AsyncMock(side_effect=AssertionError("Real networking is forbidden"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    api = EditAPI()
    reader = KankaClient(
        "offline-edit-token",
        allowed_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(api.handle),
    )
    store = EditStore(tmp_path / "private.sqlite3")
    service = EditService(
        reader,
        "offline-edit-token",
        store,
        frozenset({7}),
        EDIT_OPERATIONS,
        transport=httpx.MockTransport(api.handle),
    )
    try:
        yield service, api
    finally:
        await service.aclose()
        await reader.aclose()
    network.assert_not_awaited()
    assert service._writer.is_closed and reader._http.is_closed


def approve(service, proposal):
    service.store.approve(
        proposal["proposal_id"], proposal["review_digest"], "Local reviewer"
    )


async def run(service, edit):
    proposal = await service.prepare(edit)
    approve(service, proposal)
    return proposal, await service.execute(7, proposal["proposal_id"])


async def test_all_character_fields_resolve_module_ids_and_preserve_others(editor):
    service, api = editor
    siblings = deepcopy(api.attachments)
    changes = {
        "name": "Élan II",
        "entry": " <p>Élan &amp; lore.</p> ",
        "age": "29",
        "sex": "Non-binary",
        "race_entity_ids": [202],
        "location_entity_ids": [302],
        "tag_entity_ids": [402],
    }
    proposal, result = await run(service, draft(changes=changes))
    assert result["status"] == "verified"
    assert api.records[101]["title"] == "Scout"
    patch = [row for row in api.requests if row[0] == "PATCH"]
    assert patch == [
        (
            "PATCH",
            "campaigns/7/characters/11",
            {
                "name": "Élan II",
                "entry": changes["entry"],
                "age": "29",
                "sex": "Non-binary",
                "races": [22],
                "locations": [32],
                "tags": [42],
            },
        )
    ]
    assert api.attachments["posts"] == siblings["posts"]
    assert api.attachments["inventory"] == siblings["inventory"]
    assert api.attachments["relations"] == siblings["relations"]
    assert proposal["plan"]["diff"]["age"] == {
        "before": {"present": True, "value": "28"},
        "after": "29",
    }
    assert service.status(7, proposal["proposal_id"])["outcome"] == "confirmed"
    with pytest.raises(KankaError, match="approval"):
        await service.execute(7, proposal["proposal_id"])


@pytest.mark.parametrize(
    "kind,changes,section",
    [
        ("post", {"name": "Updated note", "entry": "<p>New.</p>"}, "posts"),
        (
            "inventory",
            {
                "amount": 3,
                "is_equipped": False,
                "position": "Pack",
                "item_entity_id": 502,
            },
            "inventory",
        ),
        (
            "relation",
            {
                "relation": "Old friend",
                "attitude": 50,
                "is_pinned": True,
                "colour": "#abcdef",
                "target_entity_id": 201,
            },
            "relations",
        ),
        ("tag", {"tag_entity_id": 402}, "entity_tags"),
    ],
)
async def test_specific_attachment_edit_leaves_siblings_untouched(
    editor, kind, changes, section
):
    service, api = editor
    before = deepcopy(api.attachments)
    proposal, result = await run(service, draft(kind, changes))
    assert result["status"] == "verified"
    assert api.attachments[section][1:] == before[section][1:]
    for other in api.attachments:
        if other != section:
            assert api.attachments[other] == before[other]
    assert len([request for request in api.requests if request[0] == "PATCH"]) == 1
    assert proposal["plan"]["path"].endswith(str(draft(kind, changes).record_id))


@pytest.mark.parametrize("named", [False, True])
async def test_inventory_patch_binds_required_unchanged_fields(editor, named):
    service, api = editor
    record = api.attachments["inventory"][0]
    if named:
        record.update(item_id=None, name="Named equipment")
    proposal, result = await run(service, draft("inventory", {"amount": 5}))
    identity = {"name": "Named equipment"} if named else {"item_id": record["item_id"]}
    assert proposal["plan"]["body"] == {"entity_id": 101, "amount": "5"} | identity
    assert set(proposal["plan"]["diff"]) == {"amount"}
    assert result["status"] == "verified"


async def test_inventory_nonquantity_edit_preserves_required_amount(editor):
    service, _api = editor
    proposal, result = await run(service, draft("inventory", {"position": "Satchel"}))
    assert proposal["plan"]["body"]["amount"] == "2"
    assert set(proposal["plan"]["diff"]) == {"position"}
    assert result["status"] == "verified"


async def test_character_edit_preserves_explicit_null_description(editor):
    service, api = editor
    api.records[101]["entry"] = None
    _proposal, result = await run(service, draft("character", {"age": "45"}))
    assert result["status"] == "verified"
    assert api.records[101]["entry"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"age": None},
        {"entry": " "},
        {"age": 28},
        {"name": True},
        {"race_entity_ids": [True]},
        {"tag_entity_ids": [401, 401]},
        {"location_entity_ids": [{}]},
        {"is_private": True},
        {"tags": [41]},
        {"arbitrary": "path"},
    ],
)
def test_strict_character_patch_validation(changes):
    with pytest.raises(ValidationError):
        draft(changes=changes)


@pytest.mark.parametrize(
    "kind,changes",
    [
        ("post", {"visibility_id": 1}),
        ("inventory", {"amount": 0}),
        ("inventory", {"is_equipped": "false"}),
        ("inventory", {"item_id": 51}),
        ("relation", {"two_way": True}),
        ("relation", {"owner_id": 102}),
        ("relation", {"attitude": True}),
        ("relation", {"colour": "not a colour"}),
        ("relation", {"relation": "x" * 256}),
        ("tag", {"tag_entity_id": False}),
    ],
)
def test_strict_attachment_patch_validation(kind, changes):
    with pytest.raises(ValidationError):
        draft(kind, changes)


@pytest.mark.parametrize(
    "changes", [{"age": "28"}, {"race_entity_ids": [201]}, {"tag_entity_ids": [401]}]
)
async def test_noop_never_creates_proposal_or_patch(editor, changes):
    service, api = editor
    with pytest.raises(ValueError, match="no changes"):
        await service.prepare(draft(changes=changes))
    assert all(method == "GET" for method, _, _ in api.requests)


async def test_explicit_empty_collections_are_reviewed_replacements(editor):
    service, api = editor
    proposal, result = await run(
        service,
        draft(
            changes={
                "race_entity_ids": [],
                "location_entity_ids": [],
                "tag_entity_ids": [],
            }
        ),
    )
    assert result["status"] == "verified"
    assert proposal["plan"]["body"] == {"races": [], "locations": [], "tags": []}
    assert api.records[101]["age"] == "28"


@pytest.mark.parametrize(
    "kind,changes,operation",
    [
        ("character", {"age": "29"}, "character.update"),
        ("post", {"entry": "New"}, "post.update"),
        ("inventory", {"amount": 3}, "inventory.update"),
        ("relation", {"attitude": 0}, "relation.update"),
        ("tag", {"tag_entity_id": 402}, "tag.update"),
    ],
)
async def test_operation_scope_denied_before_any_http(editor, kind, changes, operation):
    service, api = editor
    service.operations = EDIT_OPERATIONS - {operation}
    with pytest.raises(PermissionError):
        await service.prepare(draft(kind, changes))
    assert api.requests == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry", "Intervening lore"),
        ("name", "Renamed"),
        ("is_private", True),
        ("updated_at", "version-new"),
        ("races", [22]),
        ("title", "Captain"),
    ],
)
async def test_stale_core_is_terminal_and_sends_no_patch(editor, field, value):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.records[101][field] = value
    stopped = await service.execute(7, proposal["proposal_id"])
    assert stopped["status"] == "stale"
    assert stopped["error"]["code"] == "stale_proposal"
    assert not any(method == "PATCH" for method, _, _ in api.requests)
    with pytest.raises(PermissionError):
        approve(service, proposal)


@pytest.mark.parametrize(
    "kind,changes,section",
    [
        ("post", {"entry": "New"}, "posts"),
        ("inventory", {"amount": 3}, "inventory"),
        ("relation", {"attitude": 0}, "relations"),
        ("tag", {"tag_entity_id": 402}, "entity_tags"),
    ],
)
async def test_stale_attachment_never_sends_patch(editor, kind, changes, section):
    service, api = editor
    proposal = await service.prepare(draft(kind, changes))
    approve(service, proposal)
    api.attachments[section][0]["updated_at"] = "changed"
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "stale"
    assert not any(method == "PATCH" for method, _, _ in api.requests)


@pytest.mark.parametrize(
    "changes",
    [
        {"race_entity_ids": [301]},
        {"location_entity_ids": [201]},
        {"tag_entity_ids": [501]},
        {"race_entity_ids": [21]},
        {"race_entity_ids": [999]},
    ],
)
async def test_reference_type_or_module_id_confusion_fails_closed(editor, changes):
    service, api = editor
    with pytest.raises((ValueError, KankaError)):
        await service.prepare(draft(changes=changes))
    assert not any(method == "PATCH" for method, _, _ in api.requests)


async def test_unapproved_and_wrong_campaign_execution_are_denied(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    with pytest.raises(KankaError, match="approval"):
        await service.execute(7, proposal["proposal_id"])
    with pytest.raises(PermissionError):
        await service.execute(8, proposal["proposal_id"])
    assert not any(method == "PATCH" for method, _, _ in api.requests)


async def test_known_rejection_can_resume_only_with_fresh_approval(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.failure = 403
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "failed" and result["outcome"] == "rejected"
    assert "PRIVATE UPSTREAM" not in json.dumps(result)
    api.failure = None
    with pytest.raises(KankaError):
        await service.execute(7, proposal["proposal_id"])
    approve(service, proposal)
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "verified"


async def test_lost_response_blocks_resend_and_reconciles_get_only(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.lose_response = True
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    with pytest.raises(PermissionError):
        approve(service, proposal)
    checkpoint = len(api.requests)
    evidence = await service.inspect_reconciliation(7, proposal["proposal_id"])
    assert evidence["matches_requested_result"] and evidence["origin_verified"] is False
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
    service.store.confirm_reconciled(
        proposal["proposal_id"], evidence["fingerprint"], "Origin reviewer"
    )
    approve(service, proposal)
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert len([method for method, _, _ in api.requests if method == "PATCH"]) == 1


async def test_readback_mismatch_preserves_confirmed_intent_never_repatches(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)

    def hook(request, response):
        if request.method == "PATCH":
            api.records[101]["title"] = "Changed during send"
        return response

    api.response_hook = hook
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "partial" and result["outcome"] == "confirmed"
    approve(service, proposal)
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "partial"
    assert len([method for method, _, _ in api.requests if method == "PATCH"]) == 1


async def test_incoming_relation_and_wrong_attachment_owner_are_rejected(editor):
    service, api = editor
    with pytest.raises(KankaError, match="owner"):
        await service.prepare(
            draft("relation", {"attitude": 0}).model_copy(update={"record_id": 82})
        )
    api.attachments["posts"][0]["entity_id"] = 102
    with pytest.raises(KankaError, match="owner"):
        await service.prepare(draft("post", {"entry": "New"}))


async def test_default_registration_is_unchanged_and_edit_annotations_are_honest(
    editor,
):
    service, _api = editor
    assert len(await create_server(service.reader).list_tools()) == 8
    server = create_server(service.reader, editing=service)
    tools = await server.list_tools()
    assert len(tools) == 21
    edits = [tool for tool in tools if tool.annotations.destructiveHint is True]
    assert [tool.name for tool in edits] == ["execute_edit"]
    result = await server.call_tool(
        "prepare_character_edit",
        {"campaign_id": 7, "entity_id": 101, "changes": {"age": "29"}},
    )
    assert not result.isError
    proposal = result.structuredContent["data"]
    denied = await server.call_tool(
        "execute_edit", {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
    )
    assert (
        denied.isError
        and denied.structuredContent["error"]["code"] == "approval_required"
    )


async def test_edit_storage_preserves_existing_creation_database(editor, tmp_path):
    service, _api = editor
    path = tmp_path / "private.sqlite3"
    old = LocalApprovals(path)
    proposal = old.prepare(
        CharacterDraft(campaign_id=7, name="Old", entry="Existing"),
        {"campaign": {"id": 7, "name": "Synthetic"}},
    )
    before = old.review(proposal["proposal_id"])
    await service.prepare(draft())
    assert old.review(proposal["proposal_id"]) == before
    old.approve(proposal["proposal_id"], proposal["review_digest"], "Reviewer")
    assert old.claim(proposal["proposal_id"]).draft.name == "Old"
    old.close()


async def test_two_connections_cannot_claim_one_edit(editor, tmp_path):
    service, _api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    barrier = Barrier(2)

    def claim():
        store = EditStore(tmp_path / "private.sqlite3")
        try:
            barrier.wait(timeout=5)
            try:
                store.claim(proposal["proposal_id"])
                return True
            except PermissionError:
                return False
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _index: claim(), range(2))) == [False, True]


async def test_cancellation_during_patch_is_durably_uncertain(editor):
    service, _api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    started = asyncio.Event()

    async def cancelled_send(_request):
        started.set()
        await asyncio.Event().wait()

    await service._writer.aclose()
    service._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/",
        transport=httpx.MockTransport(cancelled_send),
    )
    task = asyncio.create_task(service.execute(7, proposal["proposal_id"]))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.status(7, proposal["proposal_id"])["outcome"] == "uncertain"
    with pytest.raises(PermissionError):
        approve(service, proposal)


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, True),
        (0, False),
        (-1, False),
        (True, False),
        (1.0, False),
        ("1", False),
        (None, False),
    ],
)
def test_positive_ids_do_not_coerce(value, expected):
    assert positive(value) is expected


async def test_uncertain_write_blocks_new_proposals_for_the_same_character(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.lose_response = True
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    with pytest.raises(PermissionError, match="uncertain"):
        await service.prepare(draft(changes={"age": "29"}))
    assert len([method for method, _, _ in api.requests if method == "PATCH"]) == 1


async def test_reference_changes_after_review_stop_before_patch(editor):
    service, api = editor
    proposal = await service.prepare(draft(changes={"race_entity_ids": [202]}))
    approve(service, proposal)
    api.records[202]["name"] = "Renamed species"
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "stale"
    assert not any(method == "PATCH" for method, _, _ in api.requests)


@pytest.mark.parametrize("phase", ["prepare", "execute"])
async def test_rename_collisions_are_not_silently_resolved(editor, phase):
    service, api = editor
    if phase == "prepare":
        with pytest.raises(ValueError, match="conflicts"):
            await service.prepare(draft(changes={"name": "Mentor"}))
    else:
        proposal = await service.prepare(draft(changes={"name": "New name"}))
        approve(service, proposal)
        api.records[102]["name"] = "New name"
        result = await service.execute(7, proposal["proposal_id"])
        assert result["status"] == "failed"
    assert not any(method == "PATCH" for method, _, _ in api.requests)


@pytest.mark.parametrize(
    "problem",
    [
        "missing_pagination",
        "wrong_owner",
        "duplicate_ids",
        "missing_privacy",
        "invalid_item",
    ],
)
async def test_inventory_baseline_requires_complete_valid_identity_and_privacy(
    editor, problem
):
    service, api = editor
    if problem == "wrong_owner":
        api.attachments["inventory"][0]["entity_id"] = 102
    elif problem == "duplicate_ids":
        api.attachments["inventory"][1]["id"] = 71
    elif problem == "missing_privacy":
        api.attachments["inventory"][0].pop("visibility_id")
    elif problem == "invalid_item":
        api.attachments["inventory"][0]["item_id"] = 501
    else:

        def hook(request, response):
            if request.method == "GET" and request.url.path.endswith("/inventory"):
                return httpx.Response(200, json={"data": response.json()["data"]})
            return response

        api.response_hook = hook
    with pytest.raises(KankaError):
        await service.prepare(draft("inventory", {"amount": 3}))
    assert not any(method == "PATCH" for method, _, _ in api.requests)


@pytest.mark.parametrize(
    "kind,changes",
    [
        ("post", {"entry": "New"}),
        ("relation", {"attitude": 0}),
        ("tag", {"tag_entity_id": 402}),
    ],
)
async def test_wrong_attachment_response_id_fails_closed(editor, kind, changes):
    service, api = editor

    def hook(request, response):
        if request.method == "GET" and request.url.path.endswith(
            str(draft(kind, changes).record_id)
        ):
            payload = response.json()
            payload["data"]["id"] = True
            return httpx.Response(200, json=payload)
        return response

    api.response_hook = hook
    with pytest.raises(KankaError):
        await service.prepare(draft(kind, changes))


@pytest.mark.parametrize(
    "problem", ["invalid_json", "wrong_id", "wrong_owner", "redirect", "server_error"]
)
async def test_ambiguous_patch_responses_stay_uncertain(editor, problem):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)

    def hook(request, response):
        if request.method != "PATCH":
            return response
        if problem == "invalid_json":
            return httpx.Response(200, text="PRIVATE INVALID JSON")
        if problem == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid"})
        if problem == "server_error":
            return httpx.Response(500, text="PRIVATE STACK TRACE")
        payload = response.json()
        payload["data"]["id" if problem == "wrong_id" else "entity_id"] = 999
        return httpx.Response(200, json=payload)

    api.response_hook = hook
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    assert "PRIVATE" not in json.dumps(result)
    with pytest.raises(PermissionError):
        approve(service, proposal)


async def test_restart_preserves_uncertainty_and_blocks_reapproval(editor, tmp_path):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.lose_response = True
    await service.execute(7, proposal["proposal_id"])
    reopened = EditStore(tmp_path / "private.sqlite3")
    try:
        assert reopened.review(proposal["proposal_id"])["outcome"] == "uncertain"
        with pytest.raises(PermissionError):
            reopened.approve(
                proposal["proposal_id"], proposal["review_digest"], "Reviewer"
            )
        with pytest.raises(PermissionError):
            reopened.claim(proposal["proposal_id"])
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("method", "DELETE"),
        ("path", "campaigns/8/characters/11"),
        ("body", {"entry": "Changed"}),
        ("before", {}),
    ],
)
async def test_tampered_method_path_body_or_baseline_cannot_be_reviewed(
    editor, field, value
):
    service, _api = editor
    proposal = await service.prepare(draft())
    plan = deepcopy(proposal["plan"])
    plan[field] = value
    with service.store._db:
        service.store._db.execute(
            "UPDATE edit_proposals SET payload = ? WHERE id = ?",
            (json.dumps(plan), proposal["proposal_id"]),
        )
    with pytest.raises(PermissionError, match="integrity"):
        service.store.review(proposal["proposal_id"])


async def test_review_copy_cannot_modify_stored_edit(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    proposal["plan"]["body"]["entry"] = "Altered copy"
    proposal["plan"]["before"]["core"]["is_private"] = True
    approve(service, proposal)
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert api.records[101]["entry"] == "<p>New lore.</p>"


@pytest.mark.parametrize("guard", ["expiry", "revocation"])
async def test_expiry_and_revocation_stop_execution_without_mutating(
    editor, monkeypatch, guard
):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    if guard == "expiry":
        monkeypatch.setattr(
            "loremaster.edit_store.time.time",
            lambda: proposal["approval_expires_at"] or 10**12,
        )
    else:
        service.store.revoke(proposal["proposal_id"])
    with pytest.raises(KankaError, match="approval"):
        await service.execute(7, proposal["proposal_id"])
    assert not any(method == "PATCH" for method, _, _ in api.requests)


async def test_reconciliation_does_not_treat_baseline_or_third_state_as_success(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.failure = 500
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    with pytest.raises(KankaError, match="differs"):
        await service.inspect_reconciliation(7, proposal["proposal_id"])
    api.records[101]["entry"] = "A third party's different lore"
    with pytest.raises(KankaError, match="differs"):
        await service.inspect_reconciliation(7, proposal["proposal_id"])
    assert service.store.review(proposal["proposal_id"])["outcome"] == "uncertain"


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "1e1000000000", True, "zero"])
async def test_malformed_inventory_amounts_fail_without_patch(editor, amount):
    service, api = editor
    api.attachments["inventory"][0]["amount"] = amount
    with pytest.raises(KankaError, match="amount"):
        await service.prepare(draft("inventory", {"position": "Pack"}))
    assert not any(method == "PATCH" for method, _, _ in api.requests)


async def test_inventory_quantity_fingerprint_preserves_all_digits(editor):
    service, api = editor
    original = "123456789012345678901234567890.00100"
    api.attachments["inventory"][0]["amount"] = original
    proposal = await service.prepare(draft("inventory", {"position": "Pack"}))
    assert proposal["plan"]["before"]["record"]["amount"] == original[:-2]
    approve(service, proposal)
    api.attachments["inventory"][0]["amount"] = "123456789012345678901234567890.002"
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "stale"
    assert not any(method == "PATCH" for method, _, _ in api.requests)


async def test_an_already_prepared_edit_cannot_bypass_a_different_uncertain_intent(
    editor,
):
    service, api = editor
    first = await service.prepare(draft())
    second = await service.prepare(draft(changes={"age": "29"}))
    approve(service, first)
    api.failure = 500
    assert (await service.execute(7, first["proposal_id"]))["status"] == "uncertain"
    approve(service, second)
    api.failure = None
    result = await service.execute(7, second["proposal_id"])
    assert result["status"] == "failed" and "uncertain" in result["error"]["message"]
    assert len([method for method, _, _ in api.requests if method == "PATCH"]) == 1


async def test_manual_named_inventory_entry_can_be_edited_without_linking_an_item(
    editor,
):
    service, api = editor
    api.attachments["inventory"][0].update(item_id=None, name="Rope")
    _proposal, result = await run(service, draft("inventory", {"amount": 3}))
    assert result["status"] == "verified"
    assert api.attachments["inventory"][0]["item_id"] is None
    assert api.attachments["inventory"][0]["name"] == "Rope"
