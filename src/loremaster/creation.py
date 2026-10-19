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
    "character.create",
    "relation.create",
    "inventory.add",
    "post.create",
    "organisation.create",
    "organisation.member.add",
    "item.create",
    "character.update",
    "post.update",
    "inventory.update",
    "relation.update",
    "tag.update",
    "race.update",
    "family.create",
    "family.update",
    "quest.create",
    "quest.update",
    "map.create",
    "map.update",
    "journal.create",
    "journal.update",
    "note.create",
    "note.update",
    "location.create",
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


class EntityPostDraft(PostDraft):
    """Create one post on an existing entity, without creating another core record."""

    campaign_id: ID
    entity_id: ID


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


class OrganisationMemberDraft(DraftRecord):
    """Use a character's universal ID; the executor resolves its module ID."""

    character_entity_id: ID
    role: Text = "Member"
    is_private: bool = True
    pin_id: Annotated[int, Field(ge=0, le=3)] = 0
    status_id: Annotated[int, Field(ge=0, le=2)] = 0


class OrganisationDraft(DraftRecord):
    """Create one organisation and explicit memberships, never edit existing ones."""

    campaign_id: ID
    name: Text
    entry: Text
    is_private: bool = True
    type: Text | None = None
    members: tuple[OrganisationMemberDraft, ...] = ()

    @field_validator("members")
    @classmethod
    def unique_members(
        cls, values: tuple[OrganisationMemberDraft, ...]
    ) -> tuple[OrganisationMemberDraft, ...]:
        """One reviewed membership per character; do not silently duplicate."""
        if len(values) != len({member.character_entity_id for member in values}):
            raise ValueError("Organisation members must identify distinct characters")
        return values


class ItemDraft(DraftRecord):
    """Create a typed item record, separately from adding it to an inventory."""

    campaign_id: ID
    name: Text
    entry: Text
    is_private: bool = True
    type: Text | None = None
    price: Text | None = None
    size: Text | None = None
    weight: Text | None = None


class LoreDraft(DraftRecord):
    """Shared core metadata for the additional, explicitly supported modules."""

    campaign_id: ID
    name: Text
    entry: Text
    is_private: bool = True
    type: Text | None = None
    tag_entity_ids: tuple[ID, ...] = ()

    @field_validator(
        "tag_entity_ids", "location_entity_ids", mode="before", check_fields=False
    )
    @classmethod
    def json_reference_arrays(cls, values: Any) -> Any:
        """Freeze JSON arrays while retaining strict validation of their IDs."""
        return tuple(values) if isinstance(values, list) else values

    @field_validator("tag_entity_ids", "location_entity_ids", check_fields=False)
    @classmethod
    def unique_references(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        """Reject duplicate assignments before building a proposal."""
        if len(values) != len(set(values)):
            raise ValueError("Lore references must be unique")
        return values


class FamilyDraft(LoreDraft):
    """Create a family record; genealogical trees are separate resources."""

    location_entity_ids: tuple[ID, ...] = ()


class QuestDraft(LoreDraft):
    """Create a mission/quest with an optional universal instigator reference."""

    location_entity_ids: tuple[ID, ...] = ()
    instigator_entity_id: ID | None = None
    parent_entity_id: ID | None = None


class MapDraft(LoreDraft):
    """Create map metadata, without uploading images or creating markers/layers."""

    location_entity_id: ID | None = None
    is_real: bool = False


class JournalDraft(LoreDraft):
    """Create a diary/session journal; preserve date text without conversion."""

    location_entity_ids: tuple[ID, ...] = ()
    author_entity_id: ID | None = None
    date: Text | None = None


class NoteDraft(LoreDraft):
    """Create a standalone note, separately from a journal or entity post."""

    parent_entity_id: ID | None = None


class LocationDraft(LoreDraft):
    """Create a location's core metadata without inventing a parent hierarchy."""

    title: Text | None = None


type CreationDraft = (
    CharacterDraft
    | OrganisationDraft
    | ItemDraft
    | FamilyDraft
    | QuestDraft
    | MapDraft
    | JournalDraft
    | NoteDraft
    | LocationDraft
    | EntityPostDraft
)
DRAFT_MODELS = {
    "character": CharacterDraft,
    "organisation": OrganisationDraft,
    "item": ItemDraft,
    "family": FamilyDraft,
    "quest": QuestDraft,
    "map": MapDraft,
    "journal": JournalDraft,
    "note": NoteDraft,
    "location": LocationDraft,
    "post": EntityPostDraft,
}
CREATION_ENDPOINTS = {
    kind: "families" if kind == "family" else kind + "s" for kind in DRAFT_MODELS
}
LORE_REFERENCE_FIELDS = {
    "tag_entity_ids": ("tags", "tag"),
    "location_entity_ids": ("locations", "location"),
    "location_entity_id": ("location_id", "location"),
    "author_entity_id": ("author_id", None),
    "instigator_entity_id": ("instigator_id", None),
    "parent_entity_id": ("parent_id", None),
}


def creation_kind(draft: CreationDraft) -> str:
    """Return only explicitly implemented core types, never arbitrary endpoints."""
    for kind, model in DRAFT_MODELS.items():
        if type(draft) is model:
            return kind
    raise ValueError("Unsupported creation draft")


def validate_creation_draft(draft: CreationDraft) -> CreationDraft:
    """Revalidate immutable copies, including values introduced by model_copy."""
    return DRAFT_MODELS[creation_kind(draft)].model_validate(draft.model_dump())


def referenced_entities(draft: CreationDraft) -> list[tuple[int, str | None]]:
    """List universal references and required module types (None allows any type)."""
    if isinstance(draft, EntityPostDraft):
        return [(draft.entity_id, None)]
    if isinstance(draft, OrganisationDraft):
        return [(member.character_entity_id, "character") for member in draft.members]
    if isinstance(draft, ItemDraft):
        return []
    if isinstance(draft, LoreDraft):
        return [
            (uid, creation_kind(draft) if field == "parent_entity_id" else expected)
            for field, (_body_field, expected) in LORE_REFERENCE_FIELDS.items()
            if (value := getattr(draft, field, None)) is not None
            for uid in (value if isinstance(value, tuple) else (value,))
        ]
    return (
        [(relation.target_id, None) for relation in draft.relations]
        + [(item.item_entity_id, "item") for item in draft.inventory]
        + [(uid, "race") for uid in draft.race_entity_ids]
        + [(uid, "location") for uid in draft.location_entity_ids]
    )


class CreationScope(DraftRecord):
    """Deny by default; permit only explicit campaigns and operation classes."""

    campaign_ids: frozenset[ID] = frozenset()
    operations: frozenset[OperationClass] = frozenset()

    def check(self, draft: CreationDraft) -> None:
        """Check the whole proposal, including membership/attachment operations."""
        required = {creation_kind(draft) + ".create"}
        if isinstance(draft, CharacterDraft):
            for entries, operation in (
                (draft.relations, "relation.create"),
                (draft.inventory, "inventory.add"),
                (draft.posts, "post.create"),
            ):
                if entries:
                    required.add(operation)
        elif isinstance(draft, OrganisationDraft) and draft.members:
            required.add("organisation.member.add")
        if draft.campaign_id not in self.campaign_ids:
            raise PermissionError("Campaign is outside the creation scope")
        if not required <= self.operations:
            raise PermissionError(
                "Proposal contains operations outside the creation scope"
            )


def preview_creation(draft: CreationDraft) -> dict[str, Any]:
    """Return fixed ordered requests and a checksum, never execution authority."""
    draft = validate_creation_draft(draft)
    kind = creation_kind(draft)
    base = f"campaigns/{draft.campaign_id}"
    entity = base + "/entities/{entity_id}"
    core = draft.model_dump(
        include={
            "name",
            "entry",
            "is_private",
            "age",
            "sex",
            "type",
            "price",
            "size",
            "weight",
            "date",
            "is_real",
            "title",
            "entity_id",
            "visibility_id",
        },
        exclude_none=True,
    )
    if isinstance(draft, LoreDraft):
        for field, (body_field, expected) in LORE_REFERENCE_FIELDS.items():
            value = getattr(draft, field, None)
            if value is None or value == ():
                continue
            values = value if isinstance(value, tuple) else (value,)
            resolved = [
                uid if expected is None else f"{{{expected}_child_id:{uid}}}"
                for uid in values
            ]
            core[body_field] = resolved if isinstance(value, tuple) else resolved[0]
    if isinstance(draft, CharacterDraft):
        for field_name, references, module in (
            ("races", draft.race_entity_ids, "race"),
            ("locations", draft.location_entity_ids, "location"),
        ):
            if references:
                core[field_name] = [
                    f"{{{module}_child_id:{uid}}}" for uid in references
                ]
    operations = [
        {
            "key": kind,
            "operation": kind + ".create",
            "method": "POST",
            "path": (
                f"{base}/entities/{draft.entity_id}/posts"
                if isinstance(draft, EntityPostDraft)
                else base + f"/{CREATION_ENDPOINTS[kind]}"
            ),
            "body": core,
        }
    ]
    if isinstance(draft, OrganisationDraft):
        for index, member in enumerate(draft.members):
            reference = member.character_entity_id
            operations.append(
                {
                    "key": f"member:{index}",
                    "operation": "organisation.member.add",
                    "method": "POST",
                    "path": base
                    + "/organisations/{organisation_id}/organisation_members",
                    "body": {
                        **member.model_dump(exclude={"character_entity_id"}),
                        "organisation_id": "{organisation_id}",
                        "character_id": f"{{character_child_id:{reference}}}",
                    },
                }
            )
    elif isinstance(draft, CharacterDraft):
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
    draft: CreationDraft,
    key: str,
    *,
    entity_id: int | None = None,
    child_id: int | None = None,
    module_ids: dict[int, int] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Bind only typed IDs in fixed templates, preserving literal lore text."""
    operation = next(
        (op for op in preview_creation(draft)["operations"] if op["key"] == key), None
    )
    if operation is None:
        raise ValueError("Unknown creation step")
    path, body = operation["path"], dict(operation["body"])
    if isinstance(draft, LoreDraft):
        for field, (body_field, expected) in LORE_REFERENCE_FIELDS.items():
            value = getattr(draft, field, None)
            if value is None or value == ():
                continue
            values = value if isinstance(value, tuple) else (value,)
            resolved = [
                uid if expected is None else (module_ids or {}).get(uid)
                for uid in values
            ]
            if any(type(uid) is not int or uid <= 0 for uid in resolved):
                raise ValueError("Lore references require resolved positive module IDs")
            body[body_field] = resolved if isinstance(value, tuple) else resolved[0]
    if key.startswith("member:"):
        if type(child_id) is not int or child_id <= 0:
            raise ValueError("Membership requires a resolved organisation module ID")
        reference = draft.members[int(key.split(":")[1])].character_entity_id
        character_id = (module_ids or {}).get(reference)
        if type(character_id) is not int or character_id <= 0:
            raise ValueError("Membership requires a resolved character module ID")
        path = path.replace("{organisation_id}", str(child_id))
        body.update(organisation_id=child_id, character_id=character_id)
        return path, body
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
    if key != creation_kind(draft):
        if type(entity_id) is not int or entity_id <= 0:
            raise ValueError("Attachment requires a positive universal owner ID")
        path = path.replace("{entity_id}", str(entity_id))
        body["owner_id" if key.startswith("relation:") else "entity_id"] = entity_id
    if key.startswith("inventory:"):
        reference = draft.inventory[int(key.split(":")[1])].item_entity_id
        item_id = (module_ids or {}).get(reference)
        if type(item_id) is not int or item_id <= 0:
            raise ValueError("Inventory requires a resolved positive module item ID")
        body["item_id"] = item_id
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
    draft: CreationDraft = field(repr=False)


@dataclass
class _Pending:
    draft: CreationDraft
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

    def prepare(self, draft: CreationDraft) -> dict[str, Any]:
        """Store an immutable validated proposal; return a review copy with no grant."""
        draft = validate_creation_draft(draft)
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
