"""Finite typed edit contracts; omitted fields are never implicit removals."""

from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from loremaster.creation import ID, LORE_REFERENCE_FIELDS, DraftRecord

Nonempty = Annotated[str, StringConstraints(min_length=1)]
EditKind = Literal[
    "character",
    "race",
    "family",
    "quest",
    "map",
    "journal",
    "note",
    "post",
    "inventory",
    "relation",
    "tag",
]
CORE_EDIT_ENDPOINTS = {
    "character": "characters",
    "race": "races",
    "family": "families",
    "quest": "quests",
    "map": "maps",
    "journal": "journals",
    "note": "notes",
}
EDIT_OPERATIONS = frozenset(
    {
        "character.update",
        "post.update",
        "inventory.update",
        "relation.update",
        "tag.update",
        "race.update",
        "family.update",
        "quest.update",
        "map.update",
        "journal.update",
        "note.update",
    }
)


class Changes(DraftRecord):
    """Reject empty patches, nulls, blank text and duplicate reference IDs."""

    @model_validator(mode="before")
    @classmethod
    def explicit_values(cls, values: Any) -> Any:
        if not isinstance(values, dict) or not values:
            raise ValueError("Supply explicit fields to edit")
        for value in values.values():
            if value is None or isinstance(value, str) and not value.strip():
                raise ValueError("Null and blank edits are not supported")
        return values

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        for value in self.model_dump(exclude_unset=True).values():
            if isinstance(value, list) and len(value) != len(set(value)):
                raise ValueError("Reference IDs must be distinct")
        return self


class CharacterChanges(Changes):
    """Lists explicitly replace species, residence, family or tag assignments."""

    name: Nonempty | None = None
    entry: Nonempty | None = None
    age: Nonempty | None = None
    sex: Nonempty | None = None
    race_entity_ids: list[ID] | None = None
    location_entity_ids: list[ID] | None = None
    tag_entity_ids: list[ID] | None = None
    family_entity_ids: list[ID] | None = None


class PostChanges(Changes):
    """Edit one post's title/HTML without changing its visibility or identity."""

    name: Nonempty | None = None
    entry: Nonempty | None = None


class SpeciesChanges(Changes):
    """Edit a species description only; name, hierarchy and privacy are guarded."""

    entry: Nonempty


class LoreChanges(Changes):
    """Explicit core metadata; privacy, hierarchy and subresources stay guarded."""

    name: Nonempty | None = None
    entry: Nonempty | None = None
    type: Nonempty | None = None
    tag_entity_ids: list[ID] | None = None


class FamilyChanges(LoreChanges):
    """Edit family metadata without rewriting members or its family tree."""

    location_entity_ids: list[ID] | None = None


class QuestChanges(LoreChanges):
    """Edit a mission's core metadata and instigator, not its quest elements."""

    location_entity_ids: list[ID] | None = None
    instigator_entity_id: ID | None = None


class MapChanges(LoreChanges):
    """Edit map metadata without image uploads or marker/layer modifications."""

    location_entity_id: ID | None = None
    is_real: bool | None = None


class JournalChanges(LoreChanges):
    """Edit a diary's text, date and author without changing calendar rules."""

    location_entity_ids: list[ID] | None = None
    author_entity_id: ID | None = None
    date: Nonempty | None = None


class NoteChanges(LoreChanges):
    """Edit a standalone note rather than a post or journal."""


class InventoryChanges(Changes):
    """Edit one existing entry; item references use universal IDs."""

    item_entity_id: ID | None = None
    amount: Annotated[int, Field(strict=True, gt=0)] | None = None
    is_equipped: bool | None = None
    position: Nonempty | None = None


class RelationChanges(Changes):
    """Edit one outgoing edge, without changing its owner or mirroring it."""

    target_entity_id: ID | None = None
    relation: Annotated[Nonempty, Field(max_length=255)] | None = None
    attitude: Annotated[int, Field(strict=True, ge=-100, le=100)] | None = None
    is_pinned: bool | None = None
    colour: Annotated[str, Field(pattern=r"^#?[0-9a-fA-F]{6}$")] | None = None


class TagChanges(Changes):
    """Replace one tag assignment, not the campaign-wide tag's definition."""

    tag_entity_id: ID


CHANGE_MODELS = {
    "character": CharacterChanges,
    "race": SpeciesChanges,
    "family": FamilyChanges,
    "quest": QuestChanges,
    "map": MapChanges,
    "journal": JournalChanges,
    "note": NoteChanges,
    "post": PostChanges,
    "inventory": InventoryChanges,
    "relation": RelationChanges,
    "tag": TagChanges,
}


class EditDraft(DraftRecord):
    """One supported resource edit with strict campaign/owner/record identity."""

    campaign_id: ID
    entity_id: ID
    kind: EditKind
    record_id: ID | None = None
    changes: dict[str, Any]

    @model_validator(mode="after")
    def valid_target(self) -> Self:
        if (self.kind in CORE_EDIT_ENDPOINTS) != (self.record_id is None):
            raise ValueError("Attachment edits require their exact record ID")
        CHANGE_MODELS[self.kind].model_validate(self.changes)
        return self


def edit_path(draft: EditDraft, child_id: int) -> str:
    """Derive an endpoint from verified IDs, never accept an arbitrary path."""
    base = f"campaigns/{draft.campaign_id}"
    if draft.kind in CORE_EDIT_ENDPOINTS:
        return f"{base}/{CORE_EDIT_ENDPOINTS[draft.kind]}/{child_id}"
    section = {
        "post": "posts",
        "inventory": "inventory",
        "relation": "relations",
        "tag": "entity_tags",
    }[draft.kind]
    return f"{base}/entities/{draft.entity_id}/{section}/{draft.record_id}"


REFERENCE_FIELDS = {
    **LORE_REFERENCE_FIELDS,
    "race_entity_ids": ("races", "race"),
    "family_entity_ids": ("families", "family"),
    "location_entity_ids": ("locations", "location"),
    "tag_entity_ids": ("tags", "tag"),
    "item_entity_id": ("item_id", "item"),
    "target_entity_id": ("target_id", None),
    "tag_entity_id": ("tag_id", "tag"),
}


def build_edit_plan(
    draft: EditDraft, before: dict[str, Any], references: dict[str, Any]
) -> dict[str, Any]:
    """Bind a minimal PATCH and explicit diff to the exact reviewed baseline."""
    draft = EditDraft.model_validate(draft.model_dump())
    body = {}
    record = before["core" if draft.kind in CORE_EDIT_ENDPOINTS else "record"]
    for field, value in draft.changes.items():
        if field in REFERENCE_FIELDS:
            field, expected = REFERENCE_FIELDS[field]
            values = value if isinstance(value, list) else [value]
            resolved = []
            for uid in values:
                reference = references[str(uid)]
                if expected is not None and reference["entity_type"] != expected:
                    raise ValueError("Edit reference has an unexpected module type")
                resolved.append(uid if expected is None else reference["child_id"])
            value = sorted(resolved) if isinstance(value, list) else resolved[0]
        if field == "amount":
            value = str(value)
        if (
            field not in record
            or type(value) is not type(record[field])
            or value != record[field]
        ):
            body[field] = value
    if not body:
        raise ValueError("The requested edit makes no changes")
    changed = body.copy()
    if draft.kind == "inventory":
        # Kanka validates these creation-required fields even for PATCH.
        body.setdefault("entity_id", draft.entity_id)
        body.setdefault("amount", record["amount"])
        identity = "item_id" if record.get("item_id") is not None else "name"
        body.setdefault(identity, record[identity])
    return {
        "draft": draft.model_dump(mode="json"),
        "before": before,
        "references": references,
        "method": "PATCH",
        "path": edit_path(draft, before["core"]["id"]),
        "body": body,
        "diff": {
            key: {
                "before": {"present": key in record, "value": record.get(key)},
                "after": value,
            }
            for key, value in changed.items()
        },
    }
