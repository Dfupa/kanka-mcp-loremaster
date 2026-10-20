"""Strict host-model inputs for local, source-backed session-note previews."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from loremaster.creation import ID

Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=32768)]


class NoteRecord(BaseModel):
    """Preserve source whitespace; forbid coercions and undeclared fields."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ImportContext(NoteRecord):
    language: Annotated[str, Field(min_length=1, max_length=64)] = "und"
    locale: Literal["dmy", "mdy", "iso"] | None = None
    audience: Literal["admin", "all"] = "admin"
    note_taker: Text | None = None


class Anchor(NoteRecord):
    block_id: Key
    excerpt: Text
    before: str = ""
    after: str = ""


class TextEdit(NoteRecord):
    anchor: Anchor
    replacement: Annotated[str, Field(max_length=32768)]
    reason: Text


class NoteSession(NoteRecord):
    key: Key
    label: Text
    block_ids: Annotated[list[Key], Field(min_length=1, max_length=4096)]
    date_anchor: Anchor | None = None
    in_world_date: Anchor | None = None


class Mention(NoteRecord):
    key: Key
    session_key: Key
    anchor: Anchor
    name: Annotated[str, Field(min_length=1, max_length=1000)]
    entity_type: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    alias_anchor: Anchor | None = None
    sections: list[Literal["posts", "inventory", "relations"]] = Field(
        default_factory=list
    )


class Claim(NoteRecord):
    key: Key
    session_key: Key
    anchors: Annotated[list[Anchor], Field(min_length=1, max_length=32)]
    text: Text
    subject: Key | None = None
    attribution: Key | None = None
    mode: Literal[
        "observed", "reported", "rumor", "plan", "hypothetical", "failed", "dialogue"
    ] = "reported"
    polarity: Literal["positive", "negative", "uncertain"] = "positive"
    event_time: Text | None = None


class NoteAnalysis(NoteRecord):
    sessions: Annotated[list[NoteSession], Field(max_length=256)]
    analysed_blocks: Annotated[list[Key], Field(max_length=4096)]
    edits: Annotated[list[TextEdit], Field(max_length=1024)] = Field(
        default_factory=list
    )
    dialogue: Annotated[list[Anchor], Field(max_length=1024)] = Field(
        default_factory=list
    )
    mentions: Annotated[list[Mention], Field(max_length=512)] = Field(
        default_factory=list
    )
    claims: Annotated[list[Claim], Field(max_length=1024)] = Field(default_factory=list)


class Citation(NoteRecord):
    evidence_id: Key
    excerpt: Text


class Assessment(NoteRecord):
    claim_key: Key
    state: Literal[
        "confirmed_canon",
        "source_backed_assertion",
        "hypothesis",
        "creative_suggestion",
        "contradiction",
        "unresolved",
    ]
    explanation: Text
    citations: Annotated[list[Citation], Field(max_length=32)] = Field(
        default_factory=list
    )


class Reference(NoteRecord):
    kind: Literal["mention", "proposal"]
    key: Key


class FieldReference(NoteRecord):
    field: Literal[
        "parent_entity_id",
        "author_entity_id",
        "instigator_entity_id",
        "location_entity_id",
        "location_entity_ids",
        "race_entity_ids",
        "family_entity_ids",
        "tag_entity_ids",
        "item_entity_id",
        "target_entity_id",
    ]
    target: Reference


class BodyPart(NoteRecord):
    kind: Literal["text", "quote"]
    text: Text | None = None
    anchor: Anchor | None = None


class PublicationReview(NoteRecord):
    """Request a public candidate; only a separate terminal review can release it."""

    rationale: Text


class NoteOperation(NoteRecord):
    key: Key
    action: Literal[
        "create_entity", "create_post", "edit_entity", "edit_attachment", "deferred"
    ]
    entity_type: Annotated[str, Field(min_length=1, max_length=32)]
    session_key: Key
    claim_keys: Annotated[list[Key], Field(min_length=1, max_length=128)]
    target: Reference | None = None
    record_id: ID | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    references: Annotated[list[FieldReference], Field(max_length=32)] = Field(
        default_factory=list
    )
    body: Annotated[list[BodyPart], Field(max_length=128)] = Field(default_factory=list)
    session_text: bool = False
    audience: Literal["admin", "all"] = "admin"
    publication: PublicationReview | None = None


class NotePlan(NoteRecord):
    assessments: Annotated[list[Assessment], Field(max_length=1024)]
    operations: Annotated[list[NoteOperation], Field(max_length=512)]
