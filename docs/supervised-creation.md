# Supervised entity creation

The default MCP server remains GET-only with eight tools. Explicit configuration
adds scoped character, organization and item creation, reviewed through a local
terminal. All paths are tested with mocked HTTP and actual stdio/CLI processes.
The character path also passed an earlier authorized live run in one campaign.
Organization/member/item execution remains offline-only in this branch.

## Stored proposals and local approval

1. The typed preparation tools validate immutable character/organization/item
   drafts, check explicit campaign/operation scope, read reference identities,
   reject an existing exact name, and store the proposal. They send no POST.
   References use universal IDs; inventory item module IDs are resolved by GET.
2. `LocalApprovals` stores the draft and reviewed campaign/reference identities
   in an explicitly selected private SQLite file. Its `review_digest` binds that
   entire payload. The preview's `content_digest` binds ordered request templates.
   Neither checksum is an execution capability. Returned copies cannot edit the
   stored draft. Each proposal has a stable ID, also used as its execution ID.
3. The separate `kanka-loremaster-approve` command displays the exact stored
   review and current progress. `approve` requires an interactive terminal and
   the exact displayed proposal/digest phrase. It records the OS username and
   authorizes five minutes from confirmation. Review/approval/revocation do not
   require an API token. No approval/revocation method is an MCP tool.
4. Execution atomically consumes the approval, even across database connections.
   The executor takes its draft only from storage; callers cannot submit arbitrary
   paths, bodies, reviewer claims or flags. Each send rechecks current scope,
   expiry, revocation and the exact approved content.
5. Used, expired or interrupted approvals cannot be reused. A rejected/partial
   run needs a new explicit local review/approval. Uncertain intent must first
   be reconciled; issuing another approval cannot erase uncertainty.

**Local trust boundary:** OS access to the private file authorizes the command.
The username is attribution, not remote authentication. Same-OS administrators
and agents with equivalent shell/file access are trusted. An interactive terminal
avoids accidental headless approvals; it is not proof of a human identity. Remote
hosting, multi-user authentication and independent GM/player authorization remain
future work. Do not share a GM-scoped server/database with players.

All three creation environment settings must be supplied together:
`KANKA_CREATION_DATABASE` (absolute private file), `KANKA_WRITE_CAMPAIGN_IDS`, and
`KANKA_WRITE_OPERATIONS`. Operation classes are `character.create`,
`relation.create`, `inventory.add`, `post.create`, `organisation.create`,
`organisation.member.add`, `item.create`. Write campaigns must fit the
configured read allowlist. No database opens or extra tools register by default.
Incomplete or invalid configuration fails startup. See [README](../README.md)
for complete commands.

## Privacy and supported fields

The draft supports a character name/HTML description/privacy, outgoing relation
requests to existing entities, equipped linked inventory, and named HTML posts.
It also accepts optional text `age`/`sex` and universal `race_entity_ids` and
`location_entity_ids`. Verified species/location module IDs are used for Kanka's
`races`/`locations` arrays; reference identity/type changes stop execution. Each
supplied field is bound into the preview and checked on read-back. Array order
is not significant; duplicate input references and boolean IDs are rejected.
Unknown fields, boolean IDs and coerced privacy values fail validation. Character
privacy defaults to private; relations/posts default to admin visibility.
Inventory entry privacy is **not configurable** and uses Kanka's default; review
must show that limitation. Private core records do not prove private inventory.

The backend resolves only typed identity fields in fixed endpoint templates;
lore containing placeholder-like text remains unchanged. Scope covers the entire
proposal, including attachments/memberships. Dependencies may be created in
separate approved proposals, using returned IDs in subsequent requests. There is
no generic batch transaction, arbitrary mutation, edit, transfer, deletion or upload.

The user explicitly asked to ignore the observed mirroring despite
`two_way: false`. Do not treat it as a blocker or claim a live one-way guarantee.
Verification does not require that request-only field to be echoed in responses.
Inventory amounts accept equivalent numeric API representations; all other
approved fields are compared strictly. Collections are compared as multisets,
without relying on API ordering, and actual attachment IDs must match recorded IDs.

## Organization membership and item extension (offline only)

`prepare_organisation_creation` supports name, description, privacy, optional type
and explicit `OrganisationMemberDraft` entries. A member's `character_entity_id`
is universal. `role`, `is_private`, `pin_id` and `status_id` are bound into the
reviewed body. Organization and membership privacy default to true. Duplicate
character memberships are rejected. Members must resolve to real character
records in the selected campaign; mentions, generic relations and organization
namesakes cannot substitute for those identities.

The sequence is an organization core POST, then one POST per membership to
`organisations/{organisation.id}/organisation_members`, using organization and
character module IDs. Core/membership creation require separate scope classes.
GET-only member verification checks ownership, all pages, exact approved fields
and recorded membership IDs. Missing pagination metadata fails closed even when
the docs show a simplified single-record response. This contract remains subject
to a separately authorized future live test.

`prepare_item_creation` supports name, HTML description, privacy and optional
text `type`, `price`, `size`, `weight`. It uses `POST items`, requires `item.create`,
and verifies the generic/module identities plus every supplied field. It does
not implicitly add inventory. Additional item fields and references are outside
this slice and fail validation.

`execute_creation` accepts only campaign/proposal IDs and executes any supported
stored draft. The original `execute_character_creation` alias rejects other
draft types before consuming approval. Default startup is unchanged; opt-in
startup now registers fourteen tools. Existing persisted character payloads keep
their original format, while organization/item proposals explicitly bind their
type in the review payload. No database schema migration is required.

The extended offline stdio workflow creates mentor → item → Éldan with original
attachments → organization with both characters as members. Each proposal has a
separate terminal approval and scope check. Ten mocked POSTs create four core
records and six attachment/membership records. All HTTP transports are forced
mocks; the new organization/item paths have not been exercised live. See the
[scenario specification](synthetic-creation-validation.md) and Kanka's
[organizations](https://app.kanka.io/api-docs/1.0/organisations),
[memberships](https://app.kanka.io/api-docs/1.0/organisation-members), and
[items](https://app.kanka.io/api-docs/1.0/items) documentation.

## Execution, progress and recovery

`CreationService` owns a separate HTTPX POST client; `KankaClient` remains GET-only.
It rechecks reviewed reference identities and duplicate names before creating the
core record. Requests use the actual `/profile` quota for pacing, including POSTs,
with local request/time budgets independent of account subscription tiers.

`CreationJournal` commits resolved method/path/body intent **before** a send. It
stores the request fingerprint, campaign, reviewer/approval reference and confirmed
result IDs, never API/bearer tokens or raw upstream error bodies. The current
attempt per step is retained; there is no complete append-only audit history,
migration framework or retention policy yet. New files use mode 0600; insecure
existing files and symlinks are rejected. Use a private existing parent directory
outside Git. Stored proposals and requests may contain confidential lore.

| Outcome | Behavior |
| --- | --- |
| Confirmed success response | Validate positive universal/module/owner IDs, commit them, continue |
| Known HTTP rejection | Preserve progress, stop; require fresh approval before resuming |
| Scope, approval or reviewed identities change | Stop before the next send |
| Restart or partial-run continuation | Re-read completed fields and exact IDs; mismatches/incomplete reads block further sends |
| Timeout, lost/malformed response, cancellation during send, redirect or ambiguous HTTP failure | Preserve uncertain intent; prohibit approval and blind retry |
| Final read-back mismatch/unavailable data | Report incomplete/partial result and retained IDs; never claim verified success |
| Verified read-back | Mark proposal complete; consumed approval remains unusable |

Execution is serialized within a service; SQLite intent/claim checks also reject
competing sends of the same step across connections. No upstream idempotency key,
transaction, rollback or version lock is claimed. Identity rechecks cannot prevent
another Kanka client changing records between reads and writes. Revocation/expiry
stop later sends, but cannot retract a request already in flight.

`get_creation_status` checks campaign scope and returns local state, step outcomes,
known IDs and approval expiry without executing requests. A `claimed` proposal
with no uncertain step is still consumed and needs fresh approval after failure.
Inspect both proposal and step states; an expiry timestamp matters even if the
stored proposal label still says approved.

For uncertain outcomes, the local `reconcile` command:

1. Requires matching write configuration and a token for scoped GET-only reads.
2. Inspects an explicitly selected candidate: universal entity ID for a core
   character/organization/item; record ID for relation/inventory/post/membership. Missing, inaccessible,
   incomplete, ambiguous or mismatching evidence fails closed.
3. Displays the matching record and request fingerprint. The local reviewer must
   stop creation workers and verify the record's origin, then type the exact
   displayed reconciliation phrase. Content equality alone does not prove origin.
4. Records evidence against the current pending fingerprint. It sends no POST,
   issues no approval and does not resume. Fresh local approval is required.

A search finding no record never makes an unknown request safe to resend. Keep
uncertainty unresolved if origin cannot be established. There is no automatic
cleanup or deletion; reports preserve confirmed progress for user decisions.

## Offline rehearsal and validation evidence

The independent `scripts/offline_creation.py` retains the process-local
`ApprovalGate` and mandatory `httpx.MockTransport` on both clients. It cannot load
an API token or choose a live transport. Its CLI `--approve CONTENT_DIGEST` is a
trusted adapter for that fixed synthetic example, not a production approval API.
Process-local grants are opaque, single-use, expiring and revocable; restarting
the rehearsal does not restore them. Optional durable progress uses the shared
journal and a stable explicit execution ID.

The [frozen synthetic scenario](synthetic-creation-validation.md) still checks a
character description, mentor relation, equipped compass inventory and two posts.
`tests/test_creation_server.py` retains independent field comparisons through the
eight read tools. `tests/test_supervised_execution.py` tests the new proposal,
approval, execution and failure/reconciliation behavior with MockTransport.
`tests/test_supervised_stdio.py` launches the normal opt-in stdio entry point and
a separate approval CLI process with a real terminal, proving that storage and
approval cross process boundaries without enabling a model-callable approver.
Default server, scope, privacy, expiry, cancellation, malformed responses, replay,
uncertainty and cleanup retain regression coverage.

The earlier manual public synthetic run was accepted by the user, who reported
deleting that first batch. A later separately authorized live fixture check used
direct creation plus normal stdio read-back and durable journal reopening. That
is historical evidence for the fixture/read contracts, not live verification of
this new MCP writer. Neither campaign-specific helper nor live IDs/payloads are
retained in this public project. Development-only fixtures are excluded from the
installed wheel; retire obsolete one-off scaffolding before release while keeping
useful automated regression coverage.

After review of draft PR #3, the user separately authorized a public live run of
the new executor with an expanded biography, age, sex, species and residence.
Two synthetic fixture dependencies were seeded separately. The normal opt-in
stdio server then completed five journaled creation steps after exact local
review/approval under that user authorization. Independent field/attachment
checks, status, approval replay denial, journal reopening and shutdown passed.
This local host approval is not evidence of independent human authentication.

The first preflight was rate-limited before any character POST; after cooldown,
fresh approval continued the same immutable proposal. Extra independent reads
also completed after cooldown. Quota is shared with other callers; pacing does
not reserve it. No uncertain POST was retried. All test records remain for user
inspection, with no deletion authorization. Only token-free pass/fail evidence
belongs in public documentation; live IDs, biography and campaign payloads stay
in the private local report.

A new live execution test needs explicit campaign/operation consent and review of
the exact proposed fields/privacy. Cleanup is separately scoped. Image upload,
automatic modes, canon overwrites and broader entity workflows remain later work.
