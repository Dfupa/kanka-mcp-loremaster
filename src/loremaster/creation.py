"""Creation proposals and scoped approvals; no HTTP execution or MCP tools.

Only a trusted host may call approve/revoke. These Python methods do not
authenticate a human or provide a UI; never expose them as model-callable tools.
The gate in this module is process-local; local persistent approvals live in
loremaster.approvals. HTTP execution is separate from both approval stores.
"""

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ID = Annotated[int, Field(strict=True, gt=0)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OperationClass = Literal[
    "character.create", "relation.create", "inventory.add", "post.create"
]
MAX_PENDING_PROPOSALS = 100


class DraftRecord(BaseModel):
    """Reject unknown fields and coercions; proposals cannot be edited in place."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RelationDraft(DraftRecord):
    """Reference a universal target ID and request an outgoing relation."""

    target_id: ID
    relation: Annotated[Text, Field(max_length=255)]
    attitude: Annotated[int, Field(ge=-100, le=100)] = 0
    visibility_id: Annotated[int, Field(ge=1, le=5)] = 3


class InventoryDraft(DraftRecord):
    """Reference a universal item ID, resolved to its module ID before execution."""

    item_entity_id: ID
    amount: Annotated[int, Field(gt=0)] = 1
    is_equipped: bool = False
    position: str = "Pack"


class PostDraft(DraftRecord):
    """A named post with explicit visibility and source HTML."""

    name: Text
    entry: Text
    visibility_id: Annotated[int, Field(ge=1, le=5)] = 3


class CharacterDraft(DraftRecord):
    """The narrow character creation contract; no edits or deletes are supported."""

    campaign_id: ID
    name: Text
    entry: Text
    is_private: bool = True
    age: Text | None = None
    sex: Text | None = None
    race_entity_ids: tuple[ID, ...] = ()
    location_entity_ids: tuple[ID, ...] = ()
    relations: tuple[RelationDraft, ...] = ()
    inventory: tuple[InventoryDraft, ...] = ()
    posts: tuple[PostDraft, ...] = ()

    @field_validator("race_entity_ids", "location_entity_ids")
    @classmethod
    def unique_references(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        """Reject repeated references before review or execution."""
        if len(values) != len(set(values)):
            raise ValueError("Character references must be unique")
        return values


class CreationScope(DraftRecord):
    """Deny by default; permit only explicit campaigns and operation classes."""

    campaign_ids: frozenset[ID] = frozenset()
    operations: frozenset[OperationClass] = frozenset()

    def check(self, draft: CharacterDraft) -> None:
        """Check the entire proposal before accepting any of its operations."""
        required = {"character.create"}
        for entries, operation in (
            (draft.relations, "relation.create"),
            (draft.inventory, "inventory.add"),
            (draft.posts, "post.create"),
        ):
            if entries:
                required.add(operation)
        if draft.campaign_id not in self.campaign_ids:
            raise PermissionError("Campaign is outside the creation scope")
        if not required <= self.operations:
            raise PermissionError(
                "Proposal contains operations outside the creation scope"
            )


def preview_creation(draft: CharacterDraft) -> dict[str, Any]:
    """Return ordered requests and a content checksum, which is not authorization."""
    draft = CharacterDraft.model_validate(draft.model_dump())
    base = f"campaigns/{draft.campaign_id}"
    entity = base + "/entities/{entity_id}"
    core = draft.model_dump(
        include={"name", "entry", "is_private", "age", "sex"}, exclude_none=True
    )
    for field_name, references, kind in (
        ("races", draft.race_entity_ids, "race"),
        ("locations", draft.location_entity_ids, "location"),
    ):
        if references:
            core[field_name] = [f"{{{kind}_child_id:{uid}}}" for uid in references]
    operations = [
        {
            "key": "character",
            "operation": "character.create",
            "method": "POST",
            "path": base + "/characters",
            "body": core,
        }
    ]
    for index, relation in enumerate(draft.relations):
        operations.append(
            {
                "key": f"relation:{index}",
                "operation": "relation.create",
                "method": "POST",
                "path": entity + "/relations",
                "body": {
                    **relation.model_dump(),
                    "owner_id": "{entity_id}",
                    "two_way": False,
                },
            }
        )
    for index, item in enumerate(draft.inventory):
        operations.append(
            {
                "key": f"inventory:{index}",
                "operation": "inventory.add",
                "method": "POST",
                "path": entity + "/inventory",
                "body": {
                    "entity_id": "{entity_id}",
                    "item_id": f"{{item_child_id:{item.item_entity_id}}}",
                    "amount": str(item.amount),
                    "is_equipped": item.is_equipped,
                    "position": item.position,
                },
            }
        )
    for index, post in enumerate(draft.posts):
        operations.append(
            {
                "key": f"post:{index}",
                "operation": "post.create",
                "method": "POST",
                "path": entity + "/posts",
                "body": {**post.model_dump(), "entity_id": "{entity_id}"},
            }
        )
    proposal = {
        "mode": "proposal_only",
        "campaign_id": draft.campaign_id,
        "operations": operations,
    }
    digest = hashlib.sha256(
        json.dumps(proposal, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {**proposal, "content_digest": digest}


def resolve_creation_request(
    draft: CharacterDraft,
    key: str,
    *,
    entity_id: int | None = None,
    module_ids: dict[int, int] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Bind only typed IDs in one reviewed operation; never replace lore text."""
    operation = next(
        (op for op in preview_creation(draft)["operations"] if op["key"] == key), None
    )
    if operation is None:
        raise ValueError("Unknown creation step")
    path, body = operation["path"], dict(operation["body"])
    if key == "character":
        for field_name, references in (
            ("races", draft.race_entity_ids),
            ("locations", draft.location_entity_ids),
        ):
            if references:
                values = [(module_ids or {}).get(uid) for uid in references]
                if any(type(value) is not int or value <= 0 for value in values):
                    raise ValueError(
                        f"Character {field_name} require resolved module IDs"
                    )
                body[field_name] = values
    if key != "character":
        if type(entity_id) is not int or entity_id <= 0:
            raise ValueError("Attachment requires a positive universal owner ID")
        path = path.replace("{entity_id}", str(entity_id))
        body["owner_id" if key.startswith("relation:") else "entity_id"] = entity_id
    if key.startswith("inventory:"):
        reference = draft.inventory[int(key.split(":")[1])].item_entity_id
        child_id = (module_ids or {}).get(reference)
        if type(child_id) is not int or child_id <= 0:
            raise ValueError("Inventory requires a resolved positive module item ID")
        body["item_id"] = child_id
    return path, body


@dataclass(frozen=True)
class ApprovalGrant:
    """An opaque, single-use capability. Never log or include token in a preview."""

    proposal_id: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class ApprovedCreation:
    """The exact server-stored draft and audit identity released by a valid grant."""

    proposal_id: str
    content_digest: str
    approved_by: str
    draft: CharacterDraft = field(repr=False)


@dataclass
class _Pending:
    draft: CharacterDraft
    digest: str
    deadline: float
    approved_by: str | None = None
    token: str | None = field(default=None, repr=False)
    claimed: bool = False
    revoked: bool = False


class ApprovalGate:
    """An in-memory approval boundary for trusted host integration and offline tests.

    Prepare stores a validated copy; approve checks the reviewed checksum and
    issues a random grant; claim atomically consumes it. Scope, expiry and
    revocation are checked again before each simulated step. A new grant is
    required to resume a known rejection. Restart discards all approvals.
    """

    def __init__(
        self, scope: CreationScope | None = None, *, ttl_seconds: int = 300
    ) -> None:
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("Approval lifetime must be a positive integer")
        self._scope = CreationScope.model_validate(
            scope.model_dump() if scope is not None else {}
        )
        self._ttl = ttl_seconds
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def prepare(self, draft: CharacterDraft) -> dict[str, Any]:
        """Store an immutable validated proposal; return a review copy with no grant."""
        draft = CharacterDraft.model_validate(draft.model_dump())
        preview = preview_creation(draft)
        with self._lock:
            self._scope.check(draft)
            now = time.monotonic()
            self._pending = {
                key: value
                for key, value in self._pending.items()
                if value.deadline > now and not value.revoked
            }
            if len(self._pending) >= MAX_PENDING_PROPOSALS:
                raise RuntimeError(
                    "Too many pending proposals; wait for expiry or revoke"
                )
            proposal_id = secrets.token_urlsafe(24)
            self._pending[proposal_id] = _Pending(
                draft=draft, digest=preview["content_digest"], deadline=now + self._ttl
            )
        return {**preview, "proposal_id": proposal_id, "expires_in_seconds": self._ttl}

    def _record(self, proposal_id: str) -> _Pending:
        """Retrieve a current record while the caller holds the lock."""
        if not isinstance(proposal_id, str) or not proposal_id:
            raise PermissionError("Invalid proposal reference")
        record = self._pending.get(proposal_id)
        if record is None or record.deadline <= time.monotonic() or record.revoked:
            raise PermissionError("Proposal is missing, expired or revoked")
        return record

    def replace_scope(self, scope: CreationScope) -> None:
        """Trusted-host policy replacement also applies to already issued grants."""
        scope = CreationScope.model_validate(scope.model_dump())
        with self._lock:
            self._scope = scope

    def approve(
        self, proposal_id: str, *, reviewed_digest: str, approved_by: str
    ) -> ApprovalGrant:
        """Trusted-host entry point; never expose it to the model through MCP.

        approved_by is audit metadata supplied by that host, not authentication.
        The host must authenticate the reviewer and display the exact proposal.
        """
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise ValueError("Approval requires an identified reviewer")
        with self._lock:
            record = self._record(proposal_id)
            self._scope.check(record.draft)
            if (
                not isinstance(reviewed_digest, str)
                or not reviewed_digest.isascii()
                or not secrets.compare_digest(reviewed_digest, record.digest)
            ):
                raise PermissionError("Reviewed content does not match the proposal")
            if record.token is not None or record.claimed:
                raise PermissionError("Proposal has already been approved")
            record.approved_by = approved_by.strip()
            record.token = secrets.token_urlsafe(32)
            return ApprovalGrant(proposal_id, record.token)

    def claim(self, grant: ApprovalGrant) -> ApprovedCreation:
        """Atomically consume a valid capability and return only the stored content."""
        if not isinstance(grant, ApprovalGrant):
            raise PermissionError("A backend approval grant is required")
        with self._lock:
            record = self._record(grant.proposal_id)
            self._scope.check(record.draft)
            if (
                record.claimed
                or record.token is None
                or record.approved_by is None
                or not isinstance(grant.token, str)
                or not grant.token.isascii()
                or not secrets.compare_digest(grant.token, record.token)
            ):
                raise PermissionError("Approval grant is invalid or already consumed")
            record.claimed = True
            record.token = None
            return ApprovedCreation(
                grant.proposal_id, record.digest, record.approved_by, record.draft
            )

    def check_execution(self, approved: ApprovedCreation) -> None:
        """Recheck scope/expiry/revocation before a step; this performs no HTTP."""
        if not isinstance(approved, ApprovedCreation):
            raise PermissionError("An approved execution context is required")
        with self._lock:
            record = self._record(approved.proposal_id)
            self._scope.check(record.draft)
            if (
                not record.claimed
                or record.digest != approved.content_digest
                or record.approved_by != approved.approved_by
                or preview_creation(approved.draft)["content_digest"] != record.digest
            ):
                raise PermissionError("Execution does not match the approved proposal")

    def revoke(self, proposal_id: str) -> None:
        """Trusted-host revocation stops claims and subsequent simulated steps."""
        with self._lock:
            record = self._record(proposal_id)
            record.revoked = True
            record.token = None
