"""Backend approval contracts without HTTP, real credentials, or MCP write tools."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from loremaster import creation
from loremaster.creation import (
    ApprovalGate,
    ApprovalGrant,
    CharacterDraft,
    CreationScope,
    InventoryDraft,
    PostDraft,
    RelationDraft,
    preview_creation,
)

ALL_OPERATIONS = frozenset(
    {
        "character.create",
        "relation.create",
        "inventory.add",
        "post.create",
    }
)


@pytest.fixture
def draft():
    return CharacterDraft(
        campaign_id=7,
        name="Élan",
        entry="<p>Original description.</p>",
        relations=(RelationDraft(target_id=101, relation="Mentor"),),
        inventory=(InventoryDraft(item_entity_id=201, amount=2),),
        posts=(
            PostDraft(name="First", entry="One"),
            PostDraft(name="Second", entry="Two"),
        ),
    )


@pytest.fixture
def gate():
    return ApprovalGate(
        CreationScope(campaign_ids=frozenset({7}), operations=ALL_OPERATIONS)
    )


def grant_for(gate, draft):
    proposal = gate.prepare(draft)
    return gate.approve(
        proposal["proposal_id"],
        reviewed_digest=proposal["content_digest"],
        approved_by="authenticated-test-reviewer",
    )


def test_no_creation_is_allowed_by_default(draft):
    with pytest.raises(PermissionError, match="scope"):
        ApprovalGate().prepare(draft)


@pytest.mark.parametrize("operation", sorted(ALL_OPERATIONS))
def test_every_operation_class_requires_permission(draft, operation):
    gate = ApprovalGate(
        CreationScope(
            campaign_ids=frozenset({7}),
            operations=ALL_OPERATIONS - {operation},
        )
    )
    with pytest.raises(PermissionError, match="operations"):
        gate.prepare(draft)


def test_scope_does_not_require_permissions_for_absent_operations():
    gate = ApprovalGate(
        CreationScope(
            campaign_ids=frozenset({7}),
            operations=frozenset({"character.create"}),
        )
    )
    draft = CharacterDraft(campaign_id=7, name="Minimal", entry="Description")
    context = gate.claim(grant_for(gate, draft))
    assert context.draft == draft


def test_another_campaign_cannot_use_the_same_policy(gate, draft):
    with pytest.raises(PermissionError, match="Campaign"):
        gate.prepare(draft.model_copy(update={"campaign_id": 8}))


def test_review_copy_cannot_mutate_stored_draft(gate, draft):
    proposal = gate.prepare(draft)
    proposal["operations"][0]["body"]["entry"] = "Changed outside server"
    grant = gate.approve(
        proposal["proposal_id"],
        reviewed_digest=proposal["content_digest"],
        approved_by="Reviewer",
    )
    context = gate.claim(grant)
    assert context.draft.entry == "<p>Original description.</p>"
    assert context.draft is not draft
    assert "token" not in proposal
    assert grant.token not in repr(grant)
    assert grant.token not in repr(context)


@pytest.mark.parametrize(
    "change", ["entry", "visibility", "inventory", "relation", "post"]
)
def test_digest_binds_all_approved_content(gate, draft, change):
    before = preview_creation(draft)["content_digest"]
    changes = {
        "entry": {"entry": "Edited"},
        "visibility": {"is_private": False},
        "inventory": {"inventory": (InventoryDraft(item_entity_id=202),)},
        "relation": {"relations": (RelationDraft(target_id=102, relation="Mentor"),)},
        "post": {"posts": (PostDraft(name="First", entry="Changed"),)},
    }
    changed = draft.model_copy(update=changes[change])
    proposal = gate.prepare(changed)
    assert proposal["content_digest"] != before
    with pytest.raises(PermissionError, match="Reviewed content"):
        gate.approve(
            proposal["proposal_id"], reviewed_digest=before, approved_by="Reviewer"
        )


@pytest.mark.parametrize("bad", ["", "unapproved", "é", None, True])
def test_missing_or_invalid_review_digest_never_issues_grant(gate, draft, bad):
    proposal = gate.prepare(draft)
    with pytest.raises(PermissionError):
        gate.approve(
            proposal["proposal_id"], reviewed_digest=bad, approved_by="Reviewer"
        )


@pytest.mark.parametrize("actor", ["", "  ", None, True])
def test_approval_requires_reviewer_audit_identity(gate, draft, actor):
    proposal = gate.prepare(draft)
    with pytest.raises(ValueError, match="reviewer"):
        gate.approve(
            proposal["proposal_id"],
            reviewed_digest=proposal["content_digest"],
            approved_by=actor,
        )


def test_digest_or_foreign_grant_cannot_authorize_execution(gate, draft):
    proposal = gate.prepare(draft)
    with pytest.raises(PermissionError, match="backend approval"):
        gate.claim(proposal["content_digest"])
    with pytest.raises(PermissionError):
        gate.claim(ApprovalGrant(proposal["proposal_id"], proposal["content_digest"]))
    other = ApprovalGate(
        CreationScope(campaign_ids=frozenset({7}), operations=ALL_OPERATIONS)
    )
    foreign = grant_for(other, draft)
    with pytest.raises(PermissionError):
        gate.claim(foreign)


def test_grant_is_bound_to_proposal_and_single_use(gate, draft):
    grant = grant_for(gate, draft)
    another = gate.prepare(draft)
    with pytest.raises(PermissionError):
        gate.claim(ApprovalGrant(another["proposal_id"], grant.token))
    context = gate.claim(grant)
    assert context.approved_by == "authenticated-test-reviewer"
    assert context.content_digest == preview_creation(draft)["content_digest"]
    with pytest.raises(PermissionError, match="consumed"):
        gate.claim(grant)


def test_two_threads_cannot_claim_one_grant(gate, draft):
    grant = grant_for(gate, draft)

    def claim():
        try:
            gate.claim(grant)
            return True
        except PermissionError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == [False, True]


@pytest.mark.parametrize("stage", ["approve", "claim", "execute"])
def test_scope_narrowing_applies_to_existing_proposals_and_grants(gate, draft, stage):
    proposal = gate.prepare(draft)
    grant = context = None
    if stage != "approve":
        grant = gate.approve(
            proposal["proposal_id"],
            reviewed_digest=proposal["content_digest"],
            approved_by="Reviewer",
        )
    if stage == "execute":
        context = gate.claim(grant)
    gate.replace_scope(CreationScope())
    with pytest.raises(PermissionError, match="scope"):
        if stage == "approve":
            gate.approve(
                proposal["proposal_id"],
                reviewed_digest=proposal["content_digest"],
                approved_by="Reviewer",
            )
        elif stage == "claim":
            gate.claim(grant)
        else:
            gate.check_execution(context)
    gate.revoke(proposal["proposal_id"])


@pytest.mark.parametrize("stage", ["approve", "claim", "execute"])
def test_expiration_applies_throughout_execution(gate, draft, stage, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(creation, "time", SimpleNamespace(monotonic=lambda: now[0]))
    proposal = gate.prepare(draft)
    grant = context = None
    if stage != "approve":
        grant = gate.approve(
            proposal["proposal_id"],
            reviewed_digest=proposal["content_digest"],
            approved_by="Reviewer",
        )
    if stage == "execute":
        context = gate.claim(grant)
    now[0] = 400.0
    with pytest.raises(PermissionError, match="expired"):
        if stage == "approve":
            gate.approve(
                proposal["proposal_id"],
                reviewed_digest=proposal["content_digest"],
                approved_by="Reviewer",
            )
        elif stage == "claim":
            gate.claim(grant)
        else:
            gate.check_execution(context)


@pytest.mark.parametrize("claim_first", [False, True])
def test_revocation_blocks_claim_or_remaining_steps(gate, draft, claim_first):
    grant = grant_for(gate, draft)
    context = gate.claim(grant) if claim_first else None
    gate.revoke(grant.proposal_id)
    with pytest.raises(PermissionError, match="revoked"):
        gate.check_execution(context) if claim_first else gate.claim(grant)


def test_execution_context_cannot_replace_content(gate, draft):
    context = gate.claim(grant_for(gate, draft))
    changed = replace(context, draft=draft.model_copy(update={"is_private": False}))
    with pytest.raises(PermissionError, match="match"):
        gate.check_execution(changed)
    gate.check_execution(context)


def test_gate_revalidates_model_copy_and_construct_bypasses(gate, draft):
    forged = draft.model_copy(update={"campaign_id": True})
    with pytest.raises(ValidationError):
        gate.prepare(forged)
    invalid_scope = CreationScope.model_construct(
        campaign_ids=frozenset({True}), operations=ALL_OPERATIONS
    )
    with pytest.raises(ValidationError):
        gate.replace_scope(invalid_scope)


@pytest.mark.parametrize("ttl", [0, -1, True, "300"])
def test_invalid_approval_lifetimes_are_rejected(ttl):
    with pytest.raises(ValueError, match="lifetime"):
        ApprovalGate(ttl_seconds=ttl)


def test_pending_storage_is_bounded_and_revocation_frees_capacity(
    gate, draft, monkeypatch
):
    monkeypatch.setattr(creation, "MAX_PENDING_PROPOSALS", 1)
    proposal = gate.prepare(draft)
    with pytest.raises(RuntimeError, match="pending"):
        gate.prepare(draft)
    gate.revoke(proposal["proposal_id"])
    assert gate.prepare(draft)["proposal_id"] != proposal["proposal_id"]


def test_restart_discards_grants_instead_of_restoring_execution_permission(gate, draft):
    grant = grant_for(gate, draft)
    restarted = ApprovalGate(
        CreationScope(
            campaign_ids=frozenset({7}),
            operations=ALL_OPERATIONS,
        )
    )
    with pytest.raises(PermissionError, match="missing"):
        restarted.claim(grant)


def test_approval_cannot_be_reissued_for_the_same_record(gate, draft):
    proposal = gate.prepare(draft)
    grant = gate.approve(
        proposal["proposal_id"],
        reviewed_digest=proposal["content_digest"],
        approved_by="Reviewer",
    )
    with pytest.raises(PermissionError, match="already been approved"):
        gate.approve(
            proposal["proposal_id"],
            reviewed_digest=proposal["content_digest"],
            approved_by="Another reviewer",
        )
    assert gate.claim(grant).approved_by == "Reviewer"
