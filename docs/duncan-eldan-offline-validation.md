# Duncan and Éldan: offline trial and live preparation

The source is [the noisy TXT](../tests/fixtures/session_notes/duncan_eldan_noisy.txt).
The separate [ground-truth reference](../tests/fixtures/session_notes/duncan_eldan_ground_truth.md)
was used for evaluation, not passed to the ingestion service.
Source SHA-256: `7c3f71c4a17ce3eedc18e9caa77f0d83cc97758f16221cb223482765d8c3bf88`.

## What ran

`tests/duncan_eldan_fixture.py` captures host-authored anchored corrections, aliases,
claims and evidence-aware plans. `tests/test_duncan_eldan_notes.py` submits them
through the actual MCP analysis, continuation, inspection, proposal and opt-in
handoff tools. HTTPX is replaced with an invented GET-only campaign API; real
networking and every HTTP mutation are rejected. Private local SQLite stores
hold sources, snapshots and unapproved synthetic preparations.

This is a reproducible evaluation of an authored host response and the backend
contract, not a blind benchmark of a separate model or an autonomous grammar
engine. No backend model client, live entity dictionary or campaign dump was added.

## Measured results

Every case analysed all **60 source blocks**, preserved all **20 dialogue lines**
exactly once, parsed the two real session dates and required bounded continuation.
The validated edition applies **75 narration corrections** and retains **26 pending
corrections** where conservative speaker/quote guards protect narrative labels.
Full source reconstruction and the original raw bytes remain unchanged.

| Invented canonical context | Ready for review | Blocked | Already represented | Deferred | Evidence coverage |
| --- | ---: | ---: | ---: | ---: | --- |
| Matching records | 5 | 0 | 1 | 5 | Complete |
| Conflicting age/family/guild context and missing new entities | 8 | 2 | 0 | 5 | Complete |
| Duplicate Duncan records | 4 | 6 | 0 | 5 | Complete; identity remains ambiguous |
| Denied Thieves' Guild lookup | 8 | 2 | 0 | 5 | Incomplete, explicitly reported |
| Public existing records | 8 | 2 | 0 | 5 | Complete; safety blockers remain |

In the conflicting case, all eight ready operations also entered the real
supervised preparation path: two journals, two dated quest posts, Lord Bone,
the mansion location, the maze location and the gem. Every proposal stayed
**pending/unapproved**, with source provenance in its digest and no send progress.
This generated no real or synthetic HTTP writes.

The checks also confirm:

- The existing `El escape` quest is reused; no duplicate root quest is created.
- Session 01 ends trapped with the gem. Escape succeeds in session 02 only.
- Journals remain private, posts admin-only and their dates separate. No fantasy
  date, journal author or character birthday is invented.
- Failed lockpicking, the sleeping guardian, the vampire suspicion, the compass
  joke and the unaccepted delivery idea keep their attribution/modality/negation.
- No character, Torn family, second gem, ghost or Explorers membership is created
  from an acronym, translation, joke or negative mention.
- Membership facts are separately inspected through the existing organization
  member reader; prose is not mistaken for structured membership proof.
- Conflicting Duncan age and dependent background posts remain blocked.
- Existing memberships, standalone gem inventory, quest status and location
  hierarchy are explicitly deferred rather than disguised as completed writes.

## Remaining editorial limitation

Narrative labels such as `Antes de empezar:` resemble speaker prefixes to the
conservative guard. The 26 affected corrections are recorded individually with
their exact anchors and suggested replacements. They were not silently applied,
and their detection is not counted as successful correction. Coverage-complete
does not mean editorial-complete or approval to publish the journals.

## Local checks

The required development install, Ruff format, Ruff check, Ruff format-check and
full Pytest sequence passed: **921 tests**. Seven are new source-specific tests.
The source-specific command is:

```bash
.venv/bin/pytest -q tests/test_duncan_eldan_notes.py
```

The exported synthetic previews, editions, unified diffs, pending corrections and
unapproved proposals are private local review artifacts under
`/tmp/loremaster-editorial-offline-*`. These artifacts are not a release fixture
or a record of writes to Kanka. No new GitHub CI run or publication occurred.

## Live preparation boundary

After the offline checks, a one-off reader inspected the explicitly scoped test
campaign with **47 GET requests**. The supplied credential stayed in process
memory; responses and proposed exact IDs/diffs were saved outside the public
repository in a private `/tmp/loremaster-notes-live-review-*` directory. The
separate initial campaign-title discovery was GET-only as well.

That private review contains nine schema-validated creation drafts and one
minimal existing-character edit plan. The extra organization draft uses the
existing supervised organization-creation contract with two memberships; it is
explicitly separate from the note bridge's current nested-membership limitation.
These are review drafts, **not authority-store proposals or approvals**. No live
mutation ran. All duplicate and baseline checks must run afresh at preparation.

The private plan distinguishes:

1. Supported private creations and admin-only dated posts on the existing quest.
2. The proposed synthetic Éldan identity binding, requiring explicit selection
   because the source name is a partial match to the current record name.
3. Duncan's exact age, family-link and additive description corrections, pending
   review of the existing public destination. No guard or visibility was changed.
4. Exact current Explorer membership records that cannot yet be removed by the
   supported tools, and a future one-gem inventory addition needing a verified
   created item ID. Creating Thieves memberships does not remove Explorer links.
5. The guarded narration edits, and the need to label a supported-subset trial
   partial until all required facts have supported, approved structured writes.

The next live step needs the review decisions and exact scoped approval. It must
use the existing terminal review, executor stale-state checks and read-back,
stop on unknown outcomes and perform no automatic cleanup. Do not copy private
campaign evidence or credentials into this document, tests, GitHub or CI logs.

## Subsequent live attempt

The user authorized the reviewed trial and confirmed the proposed synthetic
character binding. The first journal proposal went through separate local
exact-digest approval and the actual MCP stdio executor. Kanka returned HTTP 500
on creation; durable intent remains **uncertain**, without confirmed IDs.

A GET-only name lookup found no visible matching journal. Absence from that
lookup does not establish rollback. The batch stopped, no resend occurred, and
none of the remaining creation/edit operations was attempted. There is no
verified successful write; the effect of the first POST remains unknown.

The original authority database, request, sanitized executor response and recovery
notes remain in a private `/tmp/loremaster-notes-live-execution-*` directory.
Do not rerun the helper into a fresh execution database or bypass uncertainty by
creating a replacement proposal. Reviewed GET-only reconciliation needs a matching
record with verified origin; automatic confirmed-absence reset is unavailable.

## Explicitly authorized continuation

The user subsequently instructed ignoring the failed journal and continuing.
The trusted host retained its uncertain proposal in the same authority database;
it did not retry, reconcile, clear or replace that request. Independent reviewed
operations were ordered before the second journal, with fresh target/baseline
checks and the existing separate exact-digest approval and MCP execution path.

The first independent character creation also returned HTTP 500. Its own durable
intent is now uncertain, and the continuation stopped. No later creation, quest
post, organization membership, character correction or second journal was sent.
No successful write has been verified for this trial. The original and continuation
reports remain private outside Git; both uncertain requests must be preserved.
This second failure does not establish that either request had no side effects,
or that all Kanka writes fail. No production safeguard was changed.
A subsequent complete filtered GET lookup through both generic entities and
the character listing found no matching candidate. No reconciliation was made.

## Further authorized live continuation

The user requested continuing the live test again. The same authority database
retained both uncertain proposals while the existing MCP editor verified the
reviewed synthetic character correction. Both dated posts on the existing quest
were also created and verified. The first post initially failed during GET
preflight with HTTP 429 and no recorded write step. After cooldown and fresh
exact-digest terminal review, the same proposal completed; it was not replaced.
Host pacing followed the reported account/API quota without changing backend
limits or authorization rules.

The next location creation had an unverifiable outcome, leaving a third uncertain
intent. Execution stopped before the remaining maze, item, organization/member
and second-journal proposals. No uncertain request was retried or reconciled.
This trial has three verified operations and remains partial. Private reports
preserve each attempt and GET-only final read-back evidence outside Git.

Final GET-only read-back passed all eight checks: the character correction and
untargeted fields, reverse family membership, preserved existing inventory,
Explorer memberships and old posts, and both new dated posts. Complete filtered
generic and location-module lookups found no matching location; this does not
prove rollback. The maze, item, organization/member and second-journal creations
remain unattempted. No new production code or tests changed during this live turn;
the preceding full offline result remains 931 passing tests, with no new CI run.

## Independent pass after the retry request

Fresh complete generic and module lookups found no matching candidates for the
three uncertain requests. They remained blocked for resend by the existing
executor. The temporary host attempted each of the four previously unsent drafts
once, using the same authority database, exact terminal review and MCP executor.
It continued after isolated failures while leaving every existing intent intact.

Private response metadata recorded HTTP 201 for the maze and item, with JSON
content type, but their results could not pass existing response verification.
The exact verification failure is not yet established. Organization and second
journal creation returned HTTP 500, and organization memberships were not sent.
No new operation reached verified completion. Complete subsequent generic and
module lookups, including private-record filters, found no matching candidates
for all seven uncertain requests. This does not establish rollback.

The trial now has three verified operations and seven uncertain core creations;
every reviewed draft has been attempted. The earlier character, item, location,
organization and journal live validations remain historical project evidence.
No production capability or authorization guard changed in this pass. The
temporary metadata hook passed an offline privacy check with mocked HTTP, and
the latest full suite remains 931 passing tests. Public documentation excludes
live IDs, payloads and private diagnostics.
