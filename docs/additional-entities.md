# Families, quests, maps, journals and notes

Implemented locally on `feat/supervised-editing`, before canon-aware session-note
proposals. The user confirmed the preceding character/attachment live edits in
Kanka's UI. Core metadata paths first passed offline validation. A subsequent
separately authorized live creation acceptance is recorded below; it does not
authorize the broader editing trial.
The user subsequently authorized a feature-branch commit, push and draft PR.
Merge and any further live campaign changes require separate authorization.

## Supported contracts

Kanka distinguishes journals (diaries/session logs), standalone notes and entity
posts. Missions use the quest module. Each type has separate creation and update
permissions; character permissions do not authorize any of them.

| Type | Endpoint | Creation tool | Editing tool | Extra fields beyond name, description, type and tags |
| --- | --- | --- | --- | --- |
| Family | `families` | `prepare_family_creation` | `prepare_family_edit` | `location_entity_ids` |
| Quest / mission | `quests` | `prepare_quest_creation` | `prepare_quest_edit` | `location_entity_ids`, `instigator_entity_id`, creation-only `parent_entity_id` |
| Map | `maps` | `prepare_map_creation` | `prepare_map_edit` | `location_entity_id`, `is_real` |
| Journal / diary | `journals` | `prepare_journal_creation` | `prepare_journal_edit` | `location_entity_ids`, `author_entity_id`, `date` |
| Note | `notes` | `prepare_note_creation` | `prepare_note_edit` | Creation-only `parent_entity_id` |
| Location | `locations` | `prepare_location_creation` | Not implemented | `title` |

Creation takes a typed `draft` object with `campaign_id`, `name`, `entry` and the
optional fields above. `is_private` defaults to true and may be explicitly chosen
at creation. Maps default to `is_real: false`. Names/descriptions are required
nonblank text; `type` is optional text. Dates are sent as literal text, subject to
Kanka validation, without inventing a fantasy-calendar conversion.

Every caller-supplied reference is a universal entity ID:

- Tags and locations are verified by type and resolved to module IDs in `tags`,
  `locations` or `location_id`.
- `instigator_entity_id` and `author_entity_id` remain universal in Kanka's
  `instigator_id` and `author_id`, following the documented entity references.
- New quests and notes may use `parent_entity_id`. The backend verifies a visible
  parent of the same module type and sends its universal ID as documented
  `parent_id`. This does not reparent an existing record or create a quest element.
  Kanka's live quest response exposes the parent in generic entity metadata while
  omitting it from module details. Verification and reconciliation read the
  generic parent explicitly; missing, malformed or conflicting parent data fails
  closed. A module field cannot substitute for missing generic parent evidence.
- Unknown, inaccessible, cross-campaign and wrong-type references stop preparation
  or execution. Referenced identity changes invalidate the reviewed proposal.

Editing takes `campaign_id`, the target's universal `entity_id` and typed
`changes`. The backend verifies its type and resolves the module ID used in PATCH.
Omitted fields remain unchanged. Nulls, blank edits and no-ops are rejected.
Tag/location arrays explicitly replace assignments, including `[]`; singular
reference clearing is not supported. Privacy cannot be changed through editing.
Renames are checked against exact existing entity names before preparation and
again before a send; ambiguous names are never silently selected.

These contracts cover core records and metadata. They do not expose family trees
or membership rewrites, quest elements, map markers/groups/layers, image uploads,
changes to existing hierarchy/status, calendar configuration or deletion. Map creation makes
a map record; it does not upload a background image. Existing returned member,
quest-element, map/image and journal-calendar fields participate in edit guards.
Subresources not returned by the core API are not independently enumerated, and
their complete preservation is not claimed from core reads alone.

## Review and execution

`prepare_entity_post_creation` adds one Posts-tab record to an existing verified
owner under `post.create`. Its typed draft contains `campaign_id`, universal
`entity_id`, `name`, `entry` and `visibility_id` (admin-only 3 by default; public 1
must be explicit). It does not create a new entity or change the main description.
Post names are checked within that owner's completely paginated post list; an
unrelated campaign entity with the same name is not a post-name collision.
Creation returns the existing owner's `entity_id` and a distinct `post_id`, not a
module `child_id`. Unknown post-creation outcomes require the exact post record ID
for GET-only reconciliation, origin review and fresh approval.

`prepare_post_edit` now accepts posts on any verified supported entity owner,
including quests, journals and notes. Exact post/owner IDs, visibility and retained
core fields remain guarded. Where returned, module-specific owner fields such as
quest instigator and journal calendar metadata also participate in the baseline.
There is no fallback from an absent post to overwriting the main description.

Use the existing explicit private database and campaign configuration. Scope each
class independently, for example:

```bash
export KANKA_WRITE_OPERATIONS='family.create,family.update,quest.create,quest.update,map.create,map.update,journal.create,journal.update,note.create,note.update'
```

Default startup still exposes eight read-only tools. Configured creation adds
thirteen tools (twenty-one total). Any configured update class also registers thirteen
edit tools, for thirty-four total in normal startup. Visible tools remain subject
to backend campaign/operation checks; registration does not grant permission.
A directly injected edit-only service exposes twenty-one tools.

Character editing additionally supports `family_entity_ids`: verified universal
family IDs resolve to Kanka's module `families` array. This explicitly replaces
the public assignment list; callers adding one family must include existing
assignments. `private_families` stays guarded and is not rewritten. Family members
are assigned through this character field, not an unsupported family-core body.

Example `prepare_journal_creation` arguments:

```json
{
  "draft": {
    "campaign_id": 123,
    "name": "Session log",
    "entry": "<p>The party reached the harbour.</p>",
    "type": "Session",
    "date": "2026-10-20",
    "is_private": true
  }
}
```

Review and approve each creation through the separate local terminal command,
then call `execute_creation` with its campaign/proposal IDs. For an edit, review
and approve with `--kind edit`, then call `execute_edit`. The compatible
`execute_character_creation` alias still rejects all non-character proposals.
No model-callable approval or reconciliation tools were added.

The existing durable intent, expiring single-use approval, stale-baseline checks,
bounded account-paced reads and independent verification remain in force. Unknown
POST/PATCH outcomes block blind retries; reviewed GET-only reconciliation and a
fresh approval are required. Confirmed edits with failed verification can only
retry reads. The live-observed exact paragraph wrapper for requested plain text
is now shared by creation and editing verification. This does not permit arbitrary
HTML rewriting or normalize untouched fields. There is no upstream transaction
or atomic version lock; a pre-send read cannot eliminate concurrent-write races.

Existing character/organization/item creation storage remains compatible. New
types bind their explicit discriminator into the stored review payload. No
database schema changes or migrations were added. `family` uses the explicit
`families` endpoint in previews, execution and reconciliation.

## Validation and next acceptance

The required local sequence passed: editable development installation, Ruff
format, Ruff check, format check and **759 passing tests**. This includes 136 new
tests beyond the previously verified 623-test editing baseline. The built wheel
matches source files, excludes development fixtures and passes credential-shaped
content checks, imports, all four tool-registration configurations and runtime
guards under `python -O`. At this checkpoint the changes were unpublished;
no GitHub CI result was claimed.

`tests/test_lore_entities.py` covers create/edit workflows for all five modules,
reference and universal/module-ID handling, private defaults, field validation,
duplicate/no-op handling, explicit assignment clearing, stale/untouched fields,
known rejections, uncertain sends and reviewed recovery. Real HTTP is forbidden.

`tests/test_lore_stdio.py` launches the normal server with mandatory mocked HTTP,
creates and edits all five types, approves each proposal through a separate
terminal process, reads results through MCP and checks replay denial, journal
reopening, token-free storage and shutdown. Its ten mocked writes prove offline
transport/governance wiring, not live Kanka behavior. Fixtures are excluded from
the installed wheel.

Location creation and character family assignment were subsequently added for
the user's explicitly requested live dataset setup. Location supports optional
title/type/tags and requires `location.create`; location editing is not implemented.
`family_entity_ids` resolves family module IDs and guards existing private-family
assignments. The full local suite passed with **766 tests**, Ruff clean. The
updated wheel passed source matching, fixture exclusion, credential scanning,
8/20/21/33 tool registrations and optimized-mode runtime guards.

Next: let the user inspect the verified records, then obtain concrete editing-test
content. No automatic cleanup or
deletion is included. Proceed to canon-aware session-note proposals after this
entity coverage is accepted.

## Authorized live creation acceptance

The user explicitly requested five public synthetic records in the existing
campaign test dataset: a location, a quest instigated by the synthetic mentor, a
diary authored by that mentor, a note and the synthetic character's family.
The run resolved the campaign and both characters afresh and checked exact-name
collisions before preparing proposals. Names and campaign-specific data stay in
the private report outside Git.

All five core records completed through normal MCP stdio, separate terminal
approval and independent GET verification. A separately approved character PATCH
assigned the new family, preserving any existing assignments and guarded profile
fields. Read-back checked both the character's family array and the family's
reverse member list. All new core records were public. No map was requested or
created; the broader family/quest/map/journal/note editing trial remains pending.

One note POST received HTTP 429 with a durably rejected outcome and no confirmed
record. The runner stopped. After cooldown, fresh local review/approval of the same
proposal allowed one retry; preflight checked for duplicate names again. The retry
completed and passed read-back. No unknown outcome was resent. This account's quota
can be shared with other clients; local pacing does not reserve upstream capacity.

All six proposals reopened as complete. Consumed-approval replay rejection,
private token-free journal and normal stdio shutdown checks passed. No deletion or
automatic cleanup occurred. The records remain available for user inspection and
later explicitly scoped editing tests. This is one account/campaign result for
the exact creation fields and family assignment, not standing write permission.

## Standalone post and child creation extension

The user clarified that the follow-up edits target separate Posts-tab records,
not the main descriptions. The three selected synthetic entities had no posts.
The test therefore requires one labeled public post on each before editing it.
Standalone post creation and non-character post editing reuse the existing
supervised approvals, journal and verification; no direct-API bypass is needed.

New quest/note drafts support a same-type universal `parent_entity_id` at creation
only. The original parent remains an existing record; the new child has its own
universal/module identity. Existing-parent changes remain out of scope.

`test_entity_posts.py` covers owner-local names, exact record/owner IDs, scope,
retained descriptions and siblings, wrong-owner responses, uncertain POST recovery
and module-specific owner guards. `test_child_creation.py` covers typed universal
parents, reference changes and invalid IDs. The expanded actual SDK stdio test
adds three separate post create/edit pairs and child parents, with separate local
approvals and reopened-complete checks: sixteen mocked writes. Required local
checks initially passed with 791 tests; after the live parent-response correction,
the full suite passed with **799 tests**, plus wheel/source/fixture/credential/import and
optimized-mode validation. Default/creation/edit-only/combined tool counts are
**8/21/21/34**. No new GitHub CI or publication was performed.

## Authorized Posts-tab and child acceptance

The user then requested two additional character assignments, edits to separate
posts on the synthetic quest/diary/note, a child quest and a child note. The user
explicitly confirmed Posts-tab records rather than main descriptions. Fresh exact
typed resolution found the existing test records and empty Posts tabs.

Normal MCP stdio and separate local terminal approvals added the residence and
family assignments, preserving prior assignments and guarded profile fields.
Reverse family read-back retained the existing member and included the new one.
Three labeled public posts were created, then edited through their exact IDs.
Independent read-back verified their content, public visibility and unchanged
owner descriptions. The two new public child entities passed their own identity
and universal parent checks. Nine proposals reopened complete; replay denial and
token-free journal checks passed. No deletion or reparenting occurred.

The child quest was initially confirmed but verification stopped because its
module response omitted `parent_id`. GET-only diagnostics showed the correct
parent in generic entity metadata. After the narrow verification fix and offline
regressions, fresh approval resumed the same confirmed proposal with reads only;
no duplicate creation was sent. The subsequent child note passed the same generic
parent check. The account was given extra pauses between operations after the
earlier rate-limit observation; this is private test pacing, not hardcoded tiers.

The private campaign-specific host/helper/report stay outside Git and the wheel.
This verifies the exact assignment, post and child-creation paths in one campaign.
It does not establish every edit field, map behavior or standing write permission.
Git publication is now separately authorized; further live changes require
concrete scope. Check the draft PR for current CI evidence.

Official API contracts:
[families](https://app.kanka.io/api-docs/1.0/families),
[quests](https://app.kanka.io/api-docs/1.0/quests),
[maps](https://app.kanka.io/api-docs/1.0/maps),
[journals](https://app.kanka.io/api-docs/1.0/journals),
[notes](https://app.kanka.io/api-docs/1.0/notes).
