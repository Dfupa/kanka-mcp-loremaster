# Supervised character and attachment editing

Status: implemented locally on `feat/supervised-editing`, based on merged PR #4
(`5f7f2ac`), after the user accepted and expanded the design. Automated validation
is offline. Separately authorized live trials verified the synthetic-mentor core
edit and five follow-up edits as recorded below; these grant no further live
writes. Publication was withheld during implementation; the user has now
authorized a feature-branch commit, push and draft PR. Merge remains with the user.

## Supported edits

Each proposal targets one existing character, a species description or one of a
character's exact attachments and
contains one minimal PATCH. The caller supplies a
campaign ID, universal entity ID and an explicit map of requested field changes.
The backend verifies the character and resolves its module ID. Name lookup can
help select the target, but cannot silently choose among duplicate names.

| Preparation tool | Editable fields | Required operation |
| --- | --- | --- |
| `prepare_character_edit` | `name`, `entry`, `age`, `sex`, `race_entity_ids`, `location_entity_ids`, `family_entity_ids`, `tag_entity_ids` | `character.update` |
| `prepare_species_edit` | `entry` for an existing species | `race.update` |
| `prepare_post_edit` | `name`, `entry` for an explicit `post_id` | `post.update` |
| `prepare_inventory_edit` | `amount`, `is_equipped`, `position`, `item_entity_id` for an explicit `inventory_id` | `inventory.update` |
| `prepare_relation_edit` | `relation`, `attitude`, `is_pinned`, `colour`, `target_entity_id` for an explicit outgoing `relation_id` | `relation.update` |
| `prepare_tag_edit` | `tag_entity_id` for an explicit `entity_tag_id` assignment | `tag.update` |

Each tool also requires `campaign_id`, the owning character's universal
`entity_id`, and a typed `changes` object. Reference fields use universal entity
IDs; the backend verifies campaign/type and resolves module IDs for races,
locations, tags and linked items. Relation targets stay universal. Attachment
IDs identify the actual post/inventory/edge/assignment, not a target or item.
Species description editing takes the species' universal entity ID and resolves
the verified race module ID; it cannot rename a species or change its hierarchy.

Missing fields mean unchanged. Nulls, blank text, coerced booleans/IDs, duplicate
references, unsupported fields and entirely unchanged patches are rejected.
An existing core description may explicitly be `null`; the baseline preserves
that empty value, while a missing or malformed description still fails closed.
Species/location/tag arrays explicitly replace assignments; `[]` clears the
selected collection and is shown in the diff. Live additions from empty lists
passed; replacement of whole nonempty lists and clearing still need separate
validation. Replacement of one identified tag assignment passed separately.
Text clearing is not supported. Inventory
quantities must be positive integers; zero is not an implicit deletion.

Each edit preserves the existing record's visibility. Inventory transfers,
entry creation/removal, shared tag-definition edits, membership changes, image
uploads and deletion are outside this slice. Incoming relation edges are rejected;
their actual owner must be selected separately. The writer never infers a relation
record ID from its target. It cannot request reciprocal changes with `two_way`.
Kanka's prior mirroring observation remains outside this slice; no one-way
guarantee is claimed. A core edit never writes an aggregate response to Kanka.

The host assumes a non-admin role unless the user explicitly identifies an admin
account and configures `KANKA_ADMIN_CAMPAIGN_IDS` for the selected campaign.
Editing a private core or an admin-only attachment stops with
`visibility_requires_admin` before PATCH; visibility is preserved, never widened.
Creator-readable visibility 3 remains allowed; admin-only visibility is 2.
Preparation and execution both check this policy. GET-only reconciliation and
actual Kanka access checks remain independent of the local role declaration.

Kanka documents `PUT/PATCH characters/{character.id}` with the creation body
parameters. Use only the chosen fields in PATCH and validate preservation of
omitted fields. Documentation alone does not prove the live partial-update
semantics, clearing behavior or conditional-request support:

- [Characters](https://app.kanka.io/api-docs/1.0/characters)
- [Species](https://app.kanka.io/api-docs/1.0/races)
- [Organizations](https://app.kanka.io/api-docs/1.0/organisations)
- [Items](https://app.kanka.io/api-docs/1.0/items)
- [Posts](https://app.kanka.io/api-docs/1.0/entities/posts)
- [Inventory](https://app.kanka.io/api-docs/1.0/entities/entity-inventory)
- [Relationships](https://app.kanka.io/api-docs/1.0/entities/connections)
- [Tag assignments](https://app.kanka.io/api-docs/1.0/entities/entity-tags)

The attachment docs specify PATCH endpoints with record IDs. Inventory baseline
reads enumerate the documented list endpoint and require complete pagination.
Posts, relationships and tag assignments use their documented detail GETs.
If a live tag detail omits its assignment ID, a fully paginated list must contain
exactly one matching record ID and agree with the detail's tag ID. A tag PATCH
response without an assignment ID must contain the expected tag ID; final GET
verification repeats the detail/list and character-tag consistency checks.
Missing or inconsistent IDs, ownership, privacy or reference metadata fails closed.
Inventory PATCHes carry the preserved owner, item ID (or unlinked item name) and
amount required by the endpoint, alongside the requested changes. Those fields
are bound into the reviewed payload; the diff lists actual changes only. No-op
requests still fail before adding these preserved fields.

Read-back accepts an exact single `<p>` wrapper around explicitly requested plain
description text, including HTML escaping of text. It does not strip HTML, accept
other formatting changes, or relax checks on untouched description fields.

## Configuration and local review

Reuse the existing three explicit settings: `KANKA_CREATION_DATABASE` (the legacy
name for the shared private SQLite file), `KANKA_WRITE_CAMPAIGN_IDS` and
`KANKA_WRITE_OPERATIONS`. Add only the needed `.update` operation classes. Default
startup remains eight read-only tools. Creation configuration alone retains
twenty-one tools; enabling any update operation adds thirteen edit tools, for thirty-four
total in normal startup. Visible creation tools still cannot run without their
separate `.create` permissions. Approval/revocation/reconciliation are not tools.

After reviewing the prepared proposal, use the separate local terminal command:

```bash
kanka-loremaster-approve --database /absolute/private/changes.sqlite3 --kind edit review PROPOSAL_ID
kanka-loremaster-approve --database /absolute/private/changes.sqlite3 --kind edit approve PROPOSAL_ID
```

Then call `execute_edit(campaign_id, proposal_id)`. Inspect `get_edit_status` after
any failure. The CLI defaults to creation for backward compatibility; an edit ID
cannot approve a creation proposal or vice versa. Local OS file access remains
the authority boundary, not independent human or remote authentication.

## Proposal, approval and execution

1. Resolve the target by universal ID and confirm campaign, module type, module
   ID, name and access. Read its current module fields; unavailable/malformed
   data blocks preparation. A rename must check for conflicting exact names.
2. Save an immutable proposal containing the verified identity, requested field
   map, before/after values, observed `updated_at` when available, and a
   deterministic fingerprint of the reviewed supported core fields and privacy.
   Preserve missing/null distinctions. Keep lore in the private local database.
3. Return a structured field diff and source provenance. Bind both the baseline
   and proposed values into the review digest. The MCP tools
   `prepare_character_edit`, the attachment-specific preparations above,
   `execute_edit` and `get_edit_status` expose this contract.
4. Reuse the separate local terminal review/approval flow, with expiring,
   single-use approvals. An edit requires its distinct `.update` operation
   class and an explicit campaign allowlist; creation permission never permits
   an edit. Keep approvals outside MCP and keep default startup read-only.
5. Immediately before PATCH, read the target again and compare identity, observed
   version metadata and the reviewed core fingerprint. Any difference blocks the
   proposal as stale. Prepare and approve a new diff; never silently rebase an
   approved edit onto changed lore. Recheck rename conflicts when applicable.
6. Commit resolved PATCH intent before sending. Bind method, campaign, target,
   module ID, body, baseline fingerprint and approval reference into its request
   fingerprint. Check scope/approval again at the send boundary.
7. Validate the response identity and independently GET the same target. Verify
   every requested value and preservation of the reviewed untouched fields.
   A mismatch or incomplete read is not verified success. Retain progress and
   explain which evidence is missing.

A read immediately before PATCH detects intervening edits already visible at
that read. It does not prevent a different client writing between that GET and
PATCH. `updated_at` is evidence, not an upstream lock. Do not claim atomic
compare-and-swap, a consistent full-entity snapshot or protection for unobserved
attachments. Investigate documented conditional-write support before promising
stronger guarantees; if the API cannot enforce a precondition, disclose the race.

## Durable progress and uncertain outcomes

Reuse the existing private SQLite security boundary, campaign scope, approval
expiry/revocation behavior, account pacing, error envelopes and shutdown handling.
`edit_models.py` validates requests and derives diffs; `editing.py` owns the
separate PATCH transport; `edit_store.py` adds one `edit_proposals` table to the
private database. It reuses the existing secure file opening and checksum helpers.
Existing creation payloads, checksums, tables and POST fingerprints remain intact.

The creation journal still binds POST and derives steps from creation drafts.
Edit review digests bind PATCH, derived path/body, before/after evidence and
verified reference identities. The edit table records approval/reviewer/expiry,
claim state, intent/result classification and reconciliation attribution. It
retains current progress, not a full append-only attempt history.

Known rejection stops the attempt and consumes its approval. A fresh approval
alone does not bypass a stale baseline. A timeout, cancellation during send,
lost/malformed response or ambiguous failure leaves uncertain intent and blocks
resend. Even an apparently idempotent field assignment could overwrite a newer
edit on retry.

An intent/uncertain edit blocks new edit proposals and sends for the same owning
character, including its other attachments. A different proposal ID cannot bypass
that unresolved write. A confirmed response followed by failed read-back also
never permits another PATCH: fresh approval can only retry verification reads.

Reconciliation is a separate local GET-only review of this same target and the
pending intent. Distinguish the reviewed baseline, requested result and a third
state. Matching values alone do not prove this operation caused them; an unchanged
record alone does not prove an in-flight request cannot still arrive. Require
stopped workers, explicit origin review and sufficient evidence, or leave the
outcome unresolved. Reconciliation neither sends PATCH nor issues approval.
Automatic rollback would itself be another overwrite and is outside this slice.

For the stored uncertain target, use the same scoped configuration and:

```bash
kanka-loremaster-approve --database /absolute/private/changes.sqlite3 --kind edit reconcile PROPOSAL_ID
```

The command refuses baseline/third-state/mismatching reads, shows matching result
evidence, and requires exact confirmation after origin review with workers stopped.
It does not accept a substitute candidate ID. Confirmed reconciliation permits
fresh local approval for read-back only. It never resets an unknown write to safe
to resend. Revocation/expiry cannot retract an in-flight request.

## Offline acceptance before any live test

Use synthetic records and mandatory mock transports. Exercise the normal stdio
server and separate terminal approval process, then read back through MCP.

- Happy path: edit a description and age, verify the exact diff, one PATCH to the
  resolved module ID, unchanged omitted fields and final durable completion.
- Expanded workflow: core fields/species/location, a tag assignment, one post,
  one inventory entry and one outgoing relation; five separate local approvals
  and five mocked PATCHes. Check neighboring records through the synthetic API
  and read back through normal MCP tools. Fixtures forbid real networking.
- Invalid/no-op inputs, Unicode/HTML preservation, wrong campaign/module IDs,
  duplicate rename, missing records, malformed data and unknown operation scope.
- No edit tools/database by default; creation-only scope cannot execute edits.
  No HTTP mutation before approval; expiry, revocation and replay fail closed.
- Change the description, name, privacy or version metadata after preparation:
  the stale proposal sends no PATCH and cannot be silently refreshed.
- Competing local claims, restart, known rejection, cancellation, lost response,
  uncertain intent, reviewed reconciliation and mismatch after read-back.
- Existing creation database compatibility and all read/creation regressions.
  Method/path/body/baseline changes must fail integrity checks.
- No POST, PUT or DELETE as an editing fallback; no unrelated attachment changes,
  token persistence, arbitrary endpoint execution or automatic rollback.

Coverage lives in `tests/test_editing.py`, `tests/test_edit_approvals.py` and
`tests/test_edit_stdio.py`; fixtures stay outside the installed wheel. No CI or
live-edit success is claimed from local mocks. Another live synthetic edit needs
user scope/content authorization. Organization/item core editing and canon-aware
session-note proposals remain later milestones.

The editing-only verification sequence (editable development install, Ruff format,
lint, format check, full Pytest) passed with **623 tests**, including the existing
460-test baseline. Wheel/source matching, fixture exclusion, credential-shaped
token scanning, wheel import/tool registration and `python -O` runtime validation
guards also passed. At that checkpoint changes were local and no new CI was invoked.

## Authorized live character-core acceptance

After accepting the implementation and safeguards, the user authorized five
specific changes to an existing synthetic mentor: name, text age/sex, an existing
species and an existing tag. Campaign and type-specific identities were resolved
again; the mentor was public with empty species/tag lists and no rename conflict.

The normal stdio MCP server prepared exactly those five fields. Unapproved
execution was denied. A separate local terminal process reviewed the immutable
proposal and approved it under the user's explicit trial authorization. One
PATCH completed with verified status. Independent MCP GET read-back confirmed all
requested values, unchanged universal/module identity and preservation of the
description, location, visibility and remaining core guard fields. Completed
status, consumed-approval replay rejection, private journal reopening, token-free
storage and normal server shutdown passed. No production fixes were required.

The local host performed approval; this is not independent human authentication.
Private reports, helpers, live IDs and campaign payloads remain outside Git. The
trial covers additions from empty species/tag lists in one account/campaign.
It does not establish nonempty replacement/clearing or individual attachment
PATCH behavior. The subsequent trial below supplies that additional evidence.
Git publication was withheld at this trial checkpoint and later authorized.

## Authorized live follow-up acceptance

The user explicitly authorized five exact edits: the synthetic character's second
post text, compass quantity, outgoing mentor relationship label, replacement of
the mentor's existing tag assignment, and an established species' description.
The last target was existing campaign lore, not a synthetic species. Fresh typed
resolution and exact attachment IDs identified every target; the second post was
selected by its fixture title, never by API ordering.

All five proposals completed through the normal stdio server, separate local
terminal approval and independent GET verification. The first post was unchanged.
Requested values and retained guard fields passed; tag read-back also checked the
character's resulting assignment list. Completed status, consumed-approval replay
denial, journal reopening and absence of the token in storage passed for all five.

The trial exposed four API details now covered offline:

- Plain text was stored inside a single HTML paragraph. The confirmed post write
  was verified again through reads only after the narrow comparison fix.
- A quantity-only inventory PATCH returned HTTP 422 and left the amount unchanged.
  That rejected proposal was revoked locally. A new reviewed proposal included
  preserved owner/item identity fields and changed only the quantity successfully.
- The tag detail and PATCH responses omitted the assignment ID. Detail/list
  corroboration and final character-tag checks verified the exact replacement.
- The existing species had an explicitly null description. Baseline validation
  now distinguishes that empty value from a missing or malformed field.

Species editing remains description-only under its separate `race.update` scope.
Regression coverage also lives in `test_species_editing.py`,
`test_edit_entry_verification.py` and `test_edit_tag_responses.py`. The actual SDK
stdio test includes species preparation/execution with HTTP mocked.

Private helpers, reports, live IDs and payloads remain outside Git and the wheel.
The credential host exited after all checks passed. No entities were deleted.
This evidence covers the exact edits in one account/campaign, not collection
clearing, arbitrary formatting rewrites or all possible update combinations.
This trial authorizes no further live writes. Git publication was authorized
separately after the subsequent post/child acceptance.

The user subsequently confirmed all five results in Kanka's UI. Before session
note proposals, the next requested extension adds family, quest, map, journal and
note creation/editing. Its separate offline evidence, supported fields and limits
are recorded in [additional entities](additional-entities.md).
