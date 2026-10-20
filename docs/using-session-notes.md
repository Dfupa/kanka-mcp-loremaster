# Local TXT/PDF session-note previews

This branch implements TXT ingestion, optional local PDF extraction and the
GET-only proposal workflow from the
[design](canon-aware-session-notes.md). A connected host model interprets the
notes; Python validates source anchors, protects dialogue, resolves identities
and renders local previews. No model SDK/key is added to the backend.

The four note tools never approve or write to Kanka. A separately enabled handoff
can prepare a selected operation for the existing supervised executor. It still
requires exact terminal approval and a separate execution request. OCR execution
remains a later milestone.

## Setup and import

Install the checked-out source with the usual editable development command.
For PDF support outside a development install, use `python -m pip install -e '.[pdf]'`.
The `dev` extra includes pypdf so PDF tests run in the required verification suite;
the base package still supports TXT without a PDF dependency.
Choose an absolute database path outside the checkout, in an existing private
directory. Keep it separate from the creation/approval database:

```bash
export KANKA_NOTES_DATABASE='/absolute/private/path/session-notes.sqlite3'
```

The normal server still needs `KANKA_API_TOKEN` and uses the existing campaign
allowlist. Enabling note storage adds four tools, for 12 on the default server
or 38 with both existing supervised services. New files are private; insecure
files, symlinks and existing unrelated application databases are rejected.
Without this setting the original tool sets and startup behavior stay unchanged.

Import a local TXT without an API token, preserving its original bytes:

```bash
kanka-loremaster-notes session.txt \
  --database /absolute/private/path/session-notes.sqlite3 \
  --campaign 7 --language es --locale dmy
```

Replace `7` with the selected campaign ID. The command returns an import ID and
document ID. For a revision/retry of that document, pass `--document-id` with the
returned document ID. Identical bytes/encoding in that lineage reuse its import;
revised bytes create a new source revision. Without explicit lineage a new
document is created, with exact-source duplicate imports flagged during inspection.
Do not infer document identity from a date/title alone.

For small pasted notes use `import_session_notes`, providing `campaign_id`, exact
`text`, `context` and an optional filename `label`. Context accepts `language`,
`locale` (`dmy`, `mdy`, `iso`), declared `audience` and `note_taker`. These are
content labels, not user/GM authentication. Inline text is preserved as received
UTF-8; it is not claimed to be the bytes of an unseen PDF or original file.

## PDF import and extraction review

Use the same token-free local command for a PDF:

```bash
kanka-loremaster-notes sessions.pdf \
  --database /absolute/private/path/session-notes.sqlite3 \
  --campaign 7 --language es --locale dmy \
  --pdf-start-page 1 --pdf-page-count 64
```

Original PDF bytes remain immutable. Each import records their SHA-256, extractor
and adapter versions, page range, per-page extracted-text hashes, warnings and
approximate text positions. Positions are baseline hints, not glyph bounding boxes.
An extraction variant identifies the page range and extraction metadata independently
of the source hash. Proposals carry that variant and their import ID.

Inspect `view: "extraction"` using `inspect_session_notes` and follow its cursors.
Page states are `text`, `text_requires_review`, `ocr_required`,
`no_extractable_text` or `extraction_failed`. Source blocks carry page numbers and
warnings. Missing or unreadable pages cannot be marked as analysed. A proposal
using an uncertain page remains blocked; other sessions can still produce drafts.
Repeated header candidates are flagged and preserved, never silently removed.

Every PDF requires visual review. Protected dialogue is exact relative to the
**extracted text**, which can differ from the page's appearance. Successful
extraction cannot establish reading order or perfect glyph mapping. Image-only,
mixed and failed pages produce page/hash-specific OCR requests with
`execution: "not_configured"`. No OCR runs, provider is called or document uploaded.

When the receipt reports `next_page`, import another range with
`--pdf-start-page` and the same `--document-id`. Each range is a separate immutable
import, not an automatic merge; its omitted ranges keep full-document coverage
incomplete. Repeating identical bytes, range and extraction metadata reuses the
import in that lineage. TXT databases migrate while preserving source IDs, raw
bytes and analysis snapshots. Encrypted PDFs require a decrypted local copy;
the importer does not accept passwords or encoding overrides for PDFs.

The adapter uses pypdf plain extraction. On the invented digital and column
fixtures, pypdf plain/layout and local `pdftotext` retained the expected phrases.
Plain mode supplies position visitors without a system-binary dependency;
this small comparison does not establish general extraction accuracy. See the
[pypdf extraction limits](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
and [visitor contract](https://pypdf.readthedocs.io/en/stable/modules/PageObject.html).

## Host workflow

1. Call `inspect_session_notes` with the campaign/import ID. Read all source pages
   using `next_cursor`. The result provides exact blocks, protected ranges and
   packaged phase-one instructions. Keep these source IDs in model responses.
2. Call `submit_session_note_analysis` with the current `expected_version` and a
   typed cumulative `analysis`: sessions, analysed blocks, anchored edits,
   additional dialogue spans, mentions/glossaries and attributed claims.
   An anchor contains `block_id`, exact `excerpt` and optional exact `before`/
   `after` context. Repeated excerpts need disambiguating context.
3. Follow the returned `work_cursor` through the same submission tool using
   `continuation` instead of `analysis` and the newest version. Lookups and
   requested post/inventory/relation pages are GET-only and bounded per call.
   Missing/inaccessible evidence remains explicit; it never means absence.
4. Inspect the `evidence` view. It includes identity resolutions, exact/partial
   candidates and server-issued citation units for verified records. Request
   related sections through each mention's `sections` list when needed. Existing
   post owners need a complete `posts` read for duplicate checks.
5. Call `prepare_session_note_proposals` with the current version, returned
   `evidence_digest` and a typed `plan`. Assessments reference claim keys and exact
   evidence excerpts. Operations reference verified mentions or local proposed
   entities; raw IDs/visibility/HTML descriptions are not accepted in `fields`.
6. Inspect `preview` and `questions`. Corrected text and a unified diff precede
   the proposed operations. Every operation carries claim/source references,
   dependencies, audience, canon assessments, status and blockers. None is approved.

Schemas are exposed by the MCP tools; no checkout Markdown is needed at runtime.
The first release accepts cumulative analysis rather than silently merging edits
from concurrent callers. Structural errors change no stored snapshot. A stale
submission returns `note_revision_conflict`; inspect and deliberately rebase it.

After restart, stored source/analysis/previews remain inspectable, but
`evidence_current` is false. Submit cumulative analysis at the latest version to
refresh GET evidence before preparing a fresh preview. Snapshot versions and
digests describe audit evidence, not an upstream atomic lock or current canon.

## Editing and organization rules

Source is never overwritten. Narration changes must identify exact source text;
overlapping edits or edits intersecting protected dialogue/speaker attribution
are rejected. Quote detection is conservative: inspect uncertain conversation
blocks and declare additional dialogue spans. Preserving an excerpt does not
prove that the statement it contains is true.

Use `body` parts for operation descriptions. Text parts are escaped; quote parts
must anchor protected source dialogue and preserve its source speaker. Use
`session_text: true` only for a new journal's full corrected account. `fields`
supplies supported metadata such as a suggested name/type; typed `references`
bind parent, author, locations and other supported entity references. Future IDs
stay as local dependencies and block concrete execution planning.

Sessions retain source order and stable logical keys. Explicit real dates are
parsed conservatively from their anchors/locale. Fantasy dates remain separate;
ambiguous dates are not sent as established journal dates. Post titles can be
derived from session/date when omitted. Same-owner/session/audience post
contributions merge and repeated claims are not duplicated.

The evidence view explicitly returns `entity_creation_suggestion` for a
source-backed named mention of a supported type when a complete visible search
finds no exact or partial match. The suggestion carries its source anchor and
requires approval; it creates no draft or write on its own. Partial matches remain
identity candidates for review. Failed/incomplete reads, unsupported types and
unverified acronym expansions do not produce creation suggestions. Suggestions
do not clear earlier uncertain writes or authorize replacement requests.

Named creation candidates undergo complete visible name search again. Existing
or plausible duplicate names block creation for review. A new quest's same-type
parent may be a verified existing quest or a proposed prerequisite. Existing
reparenting, quest status/elements, race creation, location editing, standalone
inventory additions/transfers and new relationships are deferred. Attachment
edits need exact owner/record evidence; an incoming relationship is not inferred
to be an outgoing editable edge.

New core candidates default to private, posts to admin/creator visibility (3).
An explicit `audience: "all"` plus `publication.rationale` prepares a public-copy
review: non-private new cores, public new posts and supported public-target edits.
Private sources need an explicit edited body and cannot use `session_text`.
See [the complete non-admin publication workflow](non-admin-publication.md).
`confirmed_canon`
claims also require explicit canon review; a model label/citation is not GM
authority. Other contradiction/unknown states remain visible as blockers.
Do not share this GM-scoped local store/server with players as a permission layer.

The server assumes non-admin unless the user explicitly identifies an admin
account and the host declares its campaign in `KANKA_ADMIN_CAMPAIGN_IDS`. Private
core operations receive a visible role blocker under the default; a non-admin
cannot hand them to the writer. Creator-readable posts are not blocked merely
because the role is non-admin. Private content is never published as a fallback.
To use the current private-core workflow, use an explicitly declared admin
account. Non-admin accounts can instead prepare the explicitly reviewed public
copy described above; this never reclassifies the original source or changes an
existing private destination. General creation tools also accept individually
reviewed `is_private: false` drafts.

## Optional supervised handoff

Enable this only alongside the existing supervised creation/editing settings and
separate notes/approval databases:

```bash
export KANKA_NOTES_HANDOFF='prepare'
```

This adds `prepare_session_note_handoff` and `inspect_session_note_handoff`:
27 tools with either injected supervisor or 40 with both. Existing configurations
retain their tool sets when the setting is absent. It grants no extra campaign or
operation permissions. The standard environment setup constructs the existing
creation service and adds editing when edit operations are configured.

1. Review all pages of `preview` and `questions`. Select one canonical operation
   `key`, not a merged contribution alias. Copy the current `version` and
   `preview_digest` from the result.
2. Call `prepare_session_note_handoff` with campaign/import ID, `expected_version`,
   `preview_digest` and `operation_key`. Only supported operations whose blockers
   are resolved can proceed. Existing identities and edit baselines are re-read;
   changed canon requires refreshed analysis and another preview.
3. Inspect the returned proposal using the existing local approval command:
   `kanka-loremaster-approve --database /absolute/private/path/supervised.sqlite3 review PROPOSAL_ID`.
   For edits, also pass `--kind edit`. The exact review includes source revision,
   import/version, preview digest, anchored excerpts, assessments and dependencies.
   These are included in the immutable approval digest with the payload.
4. If authorized, use the same command with `approve` in an interactive terminal.
   A public note derivative requires the displayed `publish` phrase, containing
   both the proposal and publication-review digests. The ordinary approval phrase
   cannot release it. Original source and candidate/diff remain local for review.
   Chat agreement, corrected prose and accepted claims do not issue approval.
   Then call the existing `execute_creation` or `execute_edit` tool for that exact
   proposal. The handoff itself never calls an executor.
5. Call `inspect_session_note_handoff` with `verify_result: true`. It requires a
   completed executor outcome and performs fresh GET checks before recording
   universal/child IDs or exact post/attachment IDs. Without that flag inspection
   reads local status and any **historical** verified result only.
6. Prepare a dependent creation/post only after its prerequisite completes.
   The bridge obtains its universal ID from the recorded proposal and verifies
   its current core; callers cannot supply result IDs. Each dependent concrete
   payload receives its own unapproved proposal and separate terminal review.

Private/default operations retain private-core and admin/creator-post visibility.
Explicitly public operations require publication review; public references and
sources are checked again before writing. Edits preserve actual destination
visibility. Unsupported writes, unresolved canon, uncertain PDF pages and
incoming relationships retain their blockers; this setting cannot clear them.

The notes database records intent before preparation in the authority database.
An interrupted call can recover the same deterministic proposal ID; concurrent
retries cannot reset approvals, revocations or completion. This is recovery across
two private stores, not an atomic cross-database transaction or a Kanka rollback.
Uncertain or partial executor outcomes block dependents and must use the existing
reviewed reconciliation process. The handoff cannot mark them complete or resend.

Call `inspect_session_note_handoff` with campaign/import only (omit
`operation_key`) to see the execution overview. It lists independent
`ready_operations`, source changes, unresolved prerequisites, durable execution
states and sanitized diagnostics. An uncertain parent blocks its dependents;
other reviewed operations remain visible. `execution_complete` refers to the
workflow, while `complete` refers to this local inspection result. The overview
does not execute or approve a batch. Keep per-operation review and inspect shared
quota/access failures before sending more requests.

For an uncertain core creation, `get_creation_status` with
`inspect_recovery: true` discovers candidates using GET-only name and response-ID
checks. Candidate field equality still needs origin review through the terminal
reconciliation command. Missing/inaccessible evidence never authorizes a resend.
See [live-test diagnosis and implementation fixes](live-test-diagnosis.md).

Resubmitting analysis invalidates its preview but retains handoff audit records.
Already prepared proposals remain immutable snapshots; changing notes does not
silently revoke them. Inspect their original source version and explicitly revoke
an obsolete proposal with the existing terminal command. A changed logical
operation cannot replace its prior handoff or automatically create another write;
automatic reimport-to-update mapping remains deferred. Keep stable operation keys
and review overlaps across documents; this is not global semantic deduplication.

## Bounds and current limits

The TXT implementation bounds source bytes (8 MiB), inline text and structured
submissions (128 KiB each), source blocks (16 KiB, at most 4,096), output pages
(128 KiB) and snapshots (32 MiB). These local safety bounds are unrelated to
Kanka subscription tiers. Oversized records return explicit errors; split the
input/plan rather than accepting silent truncation. Pending source remains visible
through the coverage view. `complete` describes processing/evidence coverage,
not resolution of every ambiguity or verification of semantic truth.

PDF input is also limited to 8 MiB and 4,096 total pages. One extraction selects
64 pages by default, at most 256, with a 2 MiB decoded content-stream bound per
page and 8 MiB total extracted text. A fixed local subprocess has a 30-second
wall timeout and a 32 MiB result bound. Where POSIX resource limits are available,
it additionally enforces 512 MiB address space and 20 CPU seconds; metadata reports
whether those limits were installed. Parser/resource failures surface explicitly.
The worker omits `KANKA_*` environment variables and executes no PDF actions.
It is process isolation with resource guards, not an operating-system sandbox.

Model token usage is controlled by the host. GET pacing/backoff follows the
existing client, with bounded logical work per submission. Canon evidence stores
historical scoped responses; it is refreshed after restart rather than reused
as a permanent name-ID dictionary.

The preview cannot promise semantic correctness, automatic entity disambiguation
or cross-document deduplication for externally written records. Exact-source
overlap is flagged; ambiguous existing post matches need reviewer selection.
No live campaign trial or new CI run is implied by offline fixtures.

## Local verification

A campaign-themed editorial fixture is available at
[`tests/fixtures/session_notes/duncan_eldan_noisy.txt`](../tests/fixtures/session_notes/duncan_eldan_noisy.txt),
with a separate [ground-truth reference](../tests/fixtures/session_notes/duncan_eldan_ground_truth.md).
It covers Duncan and Éldan's Thieves' Guild membership, Duncan's corrected age/
family background, a two-session maze escape and intentionally imperfect dialogue.
The reference is an evaluator answer key, not a second ingestion source. Fixture
creation alone does not establish successful preview generation or live acceptance.
Its subsequent [offline evaluation and live preparation report](duncan-eldan-offline-validation.md)
records 75 applied corrections, 26 guarded narration corrections, five canonical
contexts and source-specific unapproved handoffs. Live evidence and exact proposed
changes remain in private local artifacts.

The editable development install, Ruff format/check/format-check and full Pytest
sequence passed with **1,022 tests**. Final review added two attachment-target/
legacy-handoff cases and extended the existing dependency-readiness test. The suite
uses invented multilingual notes, mandatory mocked HTTP responses, an actual SDK
stdio subprocess and token-free CLI imports. It covers source reconstruction,
dialogue styles/attribution, ambiguous aliases/dates, canonical citations, blocked
and deferred proposals, parent dependencies, grouped posts, restart/CAS behavior,
cursor scope, timeout/continuation, privacy and private-file constraints.
PDF fixtures cover digital Unicode text, columns, repeated headers, image-only and
mixed pages, rotation, missing fonts, malformed/encrypted inputs, range variants,
cross-page dialogue, parser bounds and migration of an existing TXT store.
PDF journals are exercised through the real SDK stdio interface with mocked GETs.
The opt-in handoff also runs through real stdio and separate terminal approval
processes, sending writes only to the injected synthetic HTTP transport. Its tests
cover completed dependency IDs, exact source-bound review, concurrent/interrupted
preparation, stale baselines, privacy races, revocations and uncertainty.

Non-admin publication also runs through the real MCP stdio and separate terminal
CLI with synthetic HTTP. Coverage includes public new cores/posts and supported
edits, per-operation publication digests, source omission, grouped contributions,
exact dialogue/speakers, private canon/citation blockers, changed source privacy,
immutable handoffs and protection against replacing an uncertain private request.

The built wheel matches source, excludes test fixtures and passes credential-shaped
content scanning, installed imports, default and opt-in tool-registration configurations
and runtime guards under `python -O`, including the packaged PDF subprocess.
These checks demonstrate the local contracts;
they do not measure a host model's editorial accuracy or verify live Kanka writes.

A subsequent, separately authorized non-admin live publication trial verified four
creations through production MCP stdio and separate terminal publication review:
a public synthetic character, a dated journal and two public dated posts on that
new journal. Independent GETs matched the reviewed text/metadata and exact
dialogue; private source lines and labels were omitted. The posts used the freshly
verified journal ID. No new uncertainty occurred and all twelve older uncertain
intents remained unchanged. See [the publication guide](non-admin-publication.md)
for the scoped evidence and remaining limits; this trial did not edit existing
records or evaluate a model against unseen notes.
