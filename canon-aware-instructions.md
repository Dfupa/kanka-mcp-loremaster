# Canon-aware session-note instructions

Status: design for `feat/canon-aware-session-notes`, based on merged PR #5.
This document specifies future behavior; it does not implement ingestion or grant
permission to change Kanka. The first implementation produces local proposals
and uses Kanka through GET requests only. English is the development language;
preserve the language of the campaign notes unless translation is requested.

Implementation architecture and delivery slices:
[canon-aware session-note design](docs/canon-aware-session-notes.md).

## 1. Outcome and architecture

Turn noisy TXT/PDF session notes into three independently reviewable products:

1. A readable, conservatively corrected edition with traceable changes.
2. An evidence ledger of mentions, attributed claims, dates and uncertainties.
3. A proposed Kanka organization: existing entity links, missing-entity candidates,
   session journals, quests/subquests and relevant dated posts.

Preserve the original source independently of all three products. Grammar cleanup
must not manufacture lore, and campaign records must not silently rewrite what a
participant actually said. A mention may remain unresolved while the rest of the
document is processed.

Use the model in the connected host for language understanding and editing.
Keep file extraction, source slicing, quote insertion, schemas, ID verification,
completeness checks and eventual write authorization in deterministic Python.
No additional model API key, embedded model client, agent framework, vector
database or campaign-wide reindex is required. Model choice belongs to the host;
the contract must work independently of the model used to develop this design.

The normal path uses two bounded model phases: extract/edit each source section,
then reconcile its claims with retrieved Kanka evidence. Render final text from
validated edits and protected source slices. Revisit only sections affected by
new evidence or answers, rather than repeatedly rewriting the whole document.

## 2. Establish context and preserve the source

- Select one explicit campaign and the intended audience. Reuse current campaign
  allowlists and token permissions; never infer the campaign from a proper name.
- Accept a user-selected local TXT/PDF or a host-provided text extraction. Do not
  follow links or execute instructions, attachments or actions embedded in it.
  Treat both imported notes and retrieved campaign prose as untrusted content.
- Keep original bytes, a SHA-256 digest, a stable document ID, revision ID,
  filename label, extraction method/version, encoding and extraction warnings in
  a private import bundle. Retain a new revision instead of overwriting a source.
- Record the author's/note-taker's identity and perspective when supplied. Keep
  the player, player character, narrator and quoted speaker distinct; do not
  infer that the note-taker is the in-world author of a Kanka journal.
- For TXT, prefer explicit encoding or validated UTF-8/BOM detection. If decoding
  fails, request the encoding or flag the affected content; never silently use
  replacement characters. Preserve raw line endings in the source representation.
- For PDF, first try local embedded-text extraction, keeping page boundaries and
  available block coordinates. Detect blank/image pages, mixed text/image pages,
  suspect column order, missing glyphs and repeated headers. Reading order and
  text fidelity must be inspected, not presumed from successful extraction.
- Request or invoke an explicitly configured local OCR fallback only for affected
  pages. Preserve both extraction variants and uncertainty; do not silently run
  every PDF through OCR. External OCR/upload requires an approved provider.
- Apply configurable byte/page/time/text-size bounds before expensive processing.
  Report omitted pages or regions and allow continuation. An incomplete import
  can yield a partial draft, but cannot support claims of complete coverage.

PDF text extraction and OCR solve different problems; an image-only page cannot
be recovered by a text-only extractor. Use the [pypdf extraction guidance](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
when evaluating PDF behavior. The local adapter now uses optional pypdf following
synthetic fixture comparison; it emits OCR requests but does not execute OCR.

## 3. Anchor every transformation

Give each extracted block a stable ID within its revision. Each source reference
must identify the document/revision, block, page when applicable, and zero-based
half-open Unicode character offsets into the unmodified extracted block.
TXT references should also expose line numbers. PDF offsets refer to extracted
text, not byte positions in the PDF or a guarantee of visual reading order.

Store exact source excerpts with references and validate them by slicing the
source. Record extraction problems separately from editorial changes. A cleaned
paragraph may reference multiple blocks; every source block must be accounted
for as retained, reformatted, flagged or explicitly omitted with a reason.
Summaries may shorten content; the corrected edition must preserve its substance.

The model returns typed edits with block IDs and exact before/after excerpts,
not a replacement source document. Let Python derive offsets from those anchors;
do not rely on the model to count characters. Repeated excerpts need additional
context or selection rather than a first-match guess. Reject overlapping
incompatible edits, invalid ranges and mismatched excerpts. Keep an
original-to-corrected mapping for review and later citations.
Source checks verify fidelity and traceability, not the truth of a narrative.

## 4. Protect dialogue before correcting prose

Detect quotes and speaker-prefixed lines before editing. Let the extraction pass
identify additional conversation blocks using source anchors. Protect the whole
affected block when dialogue boundaries remain uncertain, leaving its prose
cleanup pending rather than guessing which words are dialogue.
Preserve exact spelling, punctuation, slang, grammatical errors, dialect, code
switching, names and acronyms inside confirmed dialogue by default.

Replace protected spans with stable internal references during editing, then
reinsert their exact source slices in Python. Reject edits that change or remove
protected dialogue or move it to a different speaker/scene. Do not trust the model
to reproduce a quote perfectly. Preserve speaker attribution as separate evidence;
an unresolved speaker must remain unresolved.

Outside dialogue, correct spelling, accents, agreement, punctuation and paragraph
structure only when meaning is preserved. Keep negation, modality, uncertainty,
numbers, tense, causality, point of view and intentional fragments. Do not repair
an unfamiliar fantasy name into a familiar word. Keep proposed canonical name or
acronym expansions in annotations until identity is resolved.

For damaged OCR or ambiguous wording, retain the extracted reading and add a
separate uncertainty note with its page/region. If the user requests dialogue
restoration, show the proposed restoration alongside the original and obtain a
specific decision for uncertain characters. Mark restored text as edited rather
than verbatim. PDF/OCR dialogue is exact to the extraction only until checked
against the source page. Translation is a separate optional derivative.

Escape text when rendering HTML; markup must not become executable content.
Escaping and paragraph markup may change serialization, but must preserve the
displayed quote text. Keep exact raw source separately; do not inject entity
links or normalize Unicode inside protected dialogue.

## 5. Segment sessions and interpret dates

Split first at explicit session headings, then scenes/topics. Preserve source
order while recording chronological order separately. Keep heading context with
each chunk; use bounded overlap only when a paragraph or conversation crosses a
boundary, and deduplicate overlapping evidence by source reference.

Maintain separate fields for:

| Field | Meaning |
| --- | --- |
| `session_key` | Stable session identity, independent of its display title/date |
| `session_date` | Real-world date the group played, if established |
| `in_world_date` | Literal fantasy date plus calendar reference when verified |
| `source_order` | Position in the original notes |
| `date_evidence` | Exact heading/span and any user-confirmed interpretation |

Do not convert fantasy dates to Gregorian dates or use file metadata/import time
as the session date. An explicit heading scopes its date until the next heading;
it does not date a quoted document or every event mentioned in that section.
Flashbacks, plans and recaps retain their own temporal qualifiers. Relative dates
need an established anchor and calendar rules before conversion.

Interpret ambiguous dates such as `03/04/26` only with supplied locale/context or
user confirmation. Keep unknown components unknown. Two sessions on one date
need distinct session keys; a dateless session may use its explicit number or an
undated label. Never create an apparently certain date for nicer sorting.

Kanka documents journal `date` as the session date, distinct from calendar fields;
the current draft accepts literal date text. Send a resolved real session date
only, and keep unresolved/fantasy dates in the narrative and proposal metadata.
See [Journals](https://app.kanka.io/api-docs/1.0/journals). Current journal drafts
do not support parent journals or fantasy-calendar configuration.

## 6. Extract claims and resolve names once

For each section, extract mentions, dialogue/speakers, actions, objectives,
outcomes and proposed prose edits in one structured pass. Retain source spans,
attribution, temporal scope, polarity, uncertainty and audience on each claim.
Distinguish actual actions from plans, jokes, hypotheticals, player discussion,
dreams, failed attempts and statements made by unreliable characters.

Collect a document-wide mention table before making API calls. Resolve repeated
mentions once per applicable scope. Keep aliases as evidence-backed local
bindings: campaign, document/session scope, literal alias, candidate identity,
supporting spans and confirmation state. This is an import aid, not a permanent
name-to-ID dictionary or a Kanka alias write.

Apply this resolution order:

1. Use an explicitly selected entity or user-confirmed scoped alias; verify its
   current campaign/type/identity with `get_entity`.
2. Use an explicit source glossary such as `V = Vélar` to generate a full-name
   candidate. It is evidence of the author's shorthand, not permission to write.
3. Call `find_entities_by_name` for each distinct plausible full name. Compare NFC
   and case-folded names without removing accents from identity matching.
4. Verify a unique exact candidate's actual record/type. Partial names, spelling
   variants, acronym initials and contextual similarity generate candidates only.
   Show alternatives when identity is ambiguous; never take the first result.
5. Scope pronouns and speaker initials to the scene and known participants.
   `DM`, `GM`, player initials and character initials may refer to different roles.
   A reused acronym may bind differently in another session; do not propagate it
   across the document or another campaign without evidence.

Use canonical links only after identity verification, keeping the original
mention visible in the corrected edition. Put optional expansions in surrounding
annotations; keep dialogue unchanged. A source glossary can distinguish which
Vélar was meant, but cannot resolve two same-name records without more evidence.

A complete visible search with no match yields `no_visible_match`, never proof
that an entity does not exist. A failed, truncated, forbidden or budget-limited
search yields `lookup_incomplete`/`inaccessible`. Neither permits an automatic
creation. When no exact or plausible partial/fuzzy candidate remains, explicitly
suggest a minimal new entity using the source-backed name and supported type.
Keep possible matches visible for review before proposing a duplicate. The
evidence view labels eligible no-match mentions as `entity_creation_suggestion`;
this is a suggestion, not an executable draft or authorization. Creation still
needs a validated source-backed plan and approval. An earlier uncertain write
is execution state: a no-match suggestion never clears it or permits a retry.
Keep unnamed roles, uncertain names and unsupported types as unresolved mentions.

## 7. Retrieve only the canon needed for each decision

Start with identity/core reads, then fetch the specific posts, inventory or
relations needed to assess a claim. Use `get_entity_full` with explicit sections
when it combines relevant reads; avoid its all-sections default for every name.
Inspect `complete`, `missing_data` and pagination on every relevant section.
Search hits and text mentions are not verified entity records or relationships.

Reuse the existing account-aware pacing, backoff and request budgets. Add a
document-level budget for model tokens, HTTP requests and elapsed time, with
explicit remaining work and resumable partial results. Do not infer account
tiers or expand fixed limits to simulate premium access. Complete each search
needed for an absence/duplicate decision, or leave that decision blocked.

Use a per-run cache keyed by campaign, credential/audience context, query/ID,
section and page. Record retrieval time and observed version/hash with evidence.
Invalidate affected reads after a change and refresh before eventual execution.
Reusing a model result requires the same source revision, instructions, alias
decisions and relevant canon evidence; changed inputs invalidate dependent work.
Do not expose private names, embeddings or source excerpts in public logs/caches.

## 8. Classify canon without flattening perspective or time

Assign each claim an explicit state and explanation:

| State | Required treatment |
| --- | --- |
| `confirmed_canon` | Cite an explicit authoritative record or GM confirmation and its scope |
| `source_backed_assertion` | Preserve who reported it, when and with what uncertainty |
| `hypothesis` | Show the inference and evidence; do not present it as established |
| `creative_suggestion` | Label invented names, titles or connective ideas explicitly |
| `contradiction` | Show conflicting claims, sources and overlapping temporal scope |
| `unresolved` | Explain missing identity, chronology, extraction or canon evidence |

Kanka text may itself contain rumors or conflicting perspectives. Read its
wording before classifying it as authoritative. New session notes are not
automatically confirmed canon merely because they are newer. A GM can explicitly
accept a claim without authorizing a persistent write; record those separately.

Compare subject, predicate, value, attribution and time. A character moving from
one town to another is a possible state transition; two incompatible locations
at the same established time may be a contradiction. A quoted accusation is
evidence that an accusation was made, not that its content is true. Ask about
retroactive corrections instead of silently choosing either source.

Numeric model confidence is optional review prioritization, never identity proof,
fact verification or execution authority. Group high-impact questions by shared
ambiguity and continue independent work. Deterministic validators check evidence
and structure; the model and reviewer still judge meaning and canon.

## 9. Organize Kanka records and posts conservatively

Use the following defaults as reviewable proposals, adapting to the campaign's
existing organization. Do not create a taxonomy or entity for every noun.

| Material | Proposed destination |
| --- | --- |
| Corrected session account | One journal per distinct session, unless an existing journal already owns it |
| Sustained objective with an identifiable outcome | Existing quest, or a proposed new quest |
| Distinct subordinate objective contributing to a parent | Child quest when the dependency is evidenced |
| Progress, setbacks, discoveries or relevant dialogue | Dated post on the affected quest/entity |
| Named character/place/family/organization/item | Link an existing record; propose minimal creation if justified |
| Loose reference, theory, handout or uncertain lore | Note or attributed post, when useful and explicitly scoped |
| Inventory/relationship/state change | Separate supported edit proposal only when ownership, IDs and facts are resolved |

A journey, paragraph, conversation or combat is not automatically a quest.
Describe one clear objective per proposed quest. Use posts for progress on the
same objective; propose a child only for an independently meaningful subgoal.
Mark editorially supplied titles as suggestions. Preserve alternative accounts
and abandoned/failed plans rather than converting them into accomplishments.

Create one substantive progress post per owner/session/audience group by default.
Combine repeated mentions from that group in narrative order. Split further for
different visibility, distinct long scenes or configured size bounds; show the
reason and stable part identity. Omit empty or redundant posts. Do not attach a
post to every mentioned character merely to increase cross-linking.

Display post titles such as `2026-10-17 — Session 12 — Recover the key`; use a
literal unresolved/undated label when necessary. Put session key, sourced dates,
attribution and concise provenance in the body/manifest. Post `created_at` is
API metadata, not session chronology. The current PostDraft has no date field:
use title/body dates, not an invented parameter. See [Posts](https://app.kanka.io/api-docs/1.0/entities/posts).

Keep one full corrected account in its session journal and use focused excerpts
or summaries elsewhere. Render entity links using a verified existing mechanism
or validated Kanka URLs; never invent mention syntax or IDs. Outside protected
dialogue, links may annotate resolved names. Keep source and destination links
subject to the same audience restrictions.

Current tooling supports new quest/note parents at creation, using a verified
same-type parent's universal entity ID. A child quest is a separate quest record,
not a quest element; these are different API resources. See [Quests](https://app.kanka.io/api-docs/1.0/quests).
Existing reparenting, quest status/element updates, calendar writes, race creation,
location core editing, standalone inventory additions/transfers and arbitrary
relation creation on existing owners are not supplied by this note workflow.
Keep unsupported actions visible as deferred proposals; do not invent endpoints
or approximate them by changing unrelated descriptions.

Prefer additive posts to rewriting existing canonical descriptions. When a
specific description edit is justified and requested, show a minimal before/after
diff while preserving unrelated text. Do not rewrite a whole entity from an
aggregate response. A quantity mentioned in dialogue is not an inventory count;
an acquired item without an identified owner cannot become an inventory edit.

## 10. Proposal contract, privacy and repeat imports

Return a versioned local proposal bundle containing these records:

- Source manifest, extraction warnings, coverage and protected dialogue spans.
- Sessions/dates, corrected text, edit mapping and exact source references.
- Mentions/alias decisions with resolved IDs, alternatives and lookup completeness.
- Claims with evidence state, attribution/time and relevant Kanka citations.
- Proposed operations with a stable local key, supported/deferred status, target
  or proposed-entity reference, payload/diff, evidence references, audience,
  dependencies, explanation and unresolved blockers.
- Grouped questions and a coverage/cost summary, including skipped processing.

Validate strict typed schemas, reject unknown keys and enforce campaign scoping.
Model-generated IDs must exist in verified evidence; use local proposal references
for uncreated entities instead of fabricated numeric IDs. Validate dependency
references, types and acyclicity. Do not equate structural validity with approval.

Default the account role to non-admin unless the user explicitly identifies an
admin account for the selected campaign. Never infer admin from token visibility,
a campaign role list or write permission on a public entity. Private core writes
and admin-only attachments need an explicit host admin declaration; surface the
role blocker before writing. Never widen visibility to evade that blocker.
Kanka's category 2 is admin-only; 3 includes admins and the creating user. Preserve
creator-readable posts for non-admins. A declaration is not an upstream grant.

Default new records to private and posts to admin/creator visibility (3). A derivative's
audience must fit the intersection of its sources' allowed audiences; visibility
IDs are categories, not a numeric ordering. Broader distribution needs a separately
reviewed redacted version. A public destination must not receive private facts, quotations,
citations or revealing link labels. Existing visibility remains guarded. Current
token access is not a separate player/GM authentication system; restrict the
host and its private import bundle accordingly.

For non-admin session-note publication, explicitly select `audience: all` and
provide `publication.rationale`. Prepare a suitable public body rather than
automatically widening the private source; private sources cannot use full
`session_text` publication. Public quotes must select complete protected spans
including speakers and cannot expand into unselected private context. Show the
original session, exact candidate metadata/text and redaction diff locally.
Public references and citations need classified public canon; restrict canon
citations to public core names/descriptions and public posts on public owners.
Recheck their privacy/content before writing and preserve existing target visibility.
Require the separate exact terminal `publish` acknowledgment with proposal and
publication digests. A model rationale or source audience label is never approval.
Retain private provenance locally and send only the reviewed request payload.

Use document lineage plus explicit session identity and operation role for stable
logical keys; retain source revision and content hashes separately. A changed
file hash alone must not create another session. Do not deduplicate by title/date
alone or merge genuinely distinct sessions sharing a date. Flag overlapping
content across separate imports for review rather than dropping it silently.

On repeat ingestion, compare against the private manifest and current Kanka
records/posts. Distinguish already represented content, changed content and
unresolved potential duplicates. A correction proposes an explicit diff to the
known exact post, without silently replacing unrelated entries. With no reliable
prior mapping, show possible matches and require selection. Private manifest
mapping is import provenance, not the entity name-resolution mechanism.

## 11. Eventual bridge to supervised execution

This first milestone ends with a preview. User approval of cleaned prose,
resolution of an acronym or acceptance of a lore claim is not write approval.
The existing backend remains the campaign/operation authorization boundary.

When a later integration is authorized, turn supported actions into existing
typed preparation calls. Create and verify prerequisites first; bind returned
universal IDs into dependent drafts before their exact terminal review. A parent
creation does not pre-approve a child/post payload containing its future ID.
Review target, fields, source evidence, audience, dependencies and consequences.

Reuse immutable proposals, separate local approval, pre-send stale-state checks,
durable intent and read-back verification. The import manifest records verified
entity/post IDs and outcomes after execution, without replacing that journal.
There is no cross-entity atomic transaction or automatic rollback. Stop dependent
actions on a rejection, changed baseline or unknown outcome; independent actions
may continue only under their own approvals. Reconcile uncertain outcomes through
the existing reviewed GET-only process before considering any new send.

## 12. Synthetic example and expected decisions

All names and events below are invented; this is not a live campaign fixture.

```text
Sesión 12 — 17/10/2026
Día 8 de Ceniza, año 412. L = Lía; V = Vélar.
L llego al Puente Gris. V dijo: «no pienso bolver, ni por mil monedas».
V cree q el gremio Vela Negra robo la llave, pero no lo vio.
Encargo: recuperar la llave. Antes tenemos que hablar con la barquera.

Sesión 13 — 19/10/2026
La barquera Mara nos enseño el paso. Recogimos la llave.
L: «yo no dije que fuera suya».
```

Assume one verified character match for Lía and Vélar, an existing quest named
`Recuperar la llave`, and complete visible searches without a match for Mara.

- Correct narration to `L llegó`, `V cree que ... robó` and `Mara nos enseñó`.
  Reinsert both quotes exactly, including `bolver` and lower-case opening words.
- Resolve L/V through the explicit glossary and verified records. If two Vélar
  records exist, leave V blocked and ask for selection; keep its original quote.
- Produce two session-journal proposals and two focused posts on the existing
  quest. Their dates come from their respective headings; the second session's
  in-world date remains unknown. Avoid a duplicate root quest.
- Suggest `Hablar con la barquera` as a child quest, with its suggested title
  labeled and parent dependency explained. If current quest structure already
  represents that objective, reuse it instead.
- Propose a minimal character record for Mara, with the sourced role `barquera`.
  Do not invent her surname, appearance, age, motives or species. A failed search
  would instead leave lookup incomplete and block the creation candidate.
- Keep the theft allegation attributed to Vélar. Do not mark Vela Negra guilty
  or create a hostile relationship from his suspicion. Treat `mil monedas` as
  dialogue, not a payment or inventory update.
- Record the reported recovery as a session outcome. Ownership and exact item
  identity remain unresolved; propose no inventory mutation. Quest status changes
  remain deferred until supported and separately approved.

## 13. Implementation order and acceptance

1. Implement the evidence/proposal schemas and pure transformations over supplied
   text, with private local bundles, conservative editing and quote reconstruction.
   Validate host-proposed structured edits without adding a mandatory model SDK.
2. Add GET-only resolution, targeted canon checks and proposal rendering through
   existing read tools. Prove the TXT workflow end to end before adding PDF.
3. Add an optional local PDF adapter and extraction-quality review using digital,
   scanned and mixed fixtures; add OCR only when its configuration is explicit.
4. Review the proposal workflow before connecting it to existing write executors.
   Adding the bridge or running live acceptance requires its own concrete scope.

Use synthetic multilingual notes and mocked Kanka HTTP; forbid live writes in
these tests. Maintain golden source/quote fixtures and assert:

- Every protected quote is preserved exactly, every edit maps to valid evidence,
  and no source region silently disappears. Uncertain OCR is exposed.
- Acronym collisions, duplicates, accents, player/character names and unresolved
  pronouns never produce a silently selected identity or fabricated numeric ID.
- Rumors, negation, failed plans, dialogue and time-scoped transitions retain their
  meaning; conflicting canon is surfaced with both sources.
- Ambiguous dates, fantasy calendars, flashbacks, multiple same-date sessions and
  dateless notes do not produce invented chronology or merged sessions.
- Existing quests are reused; child goals are distinct; repeated mentions produce
  one relevant owner/session/audience post, with deterministic handling of parts.
- Missing pagination, inaccessible records and budget exhaustion produce explicit
  partial results and prevent unsupported absence/duplicate conclusions.
- Reimporting identical input proposes no duplicate work; revised and overlapping
  documents show reviewable changes and preserve prior session identity.
- Private content and evidence cannot enter a public proposal without an explicit
  redacted version; source text cannot instruct tool use or grant approval.
- First-milestone execution is GET-only. Dependency cycles, unsupported actions,
  invented IDs and attempts to bypass existing approval boundaries are rejected.

Measure source coverage, quote fidelity, accepted/corrected/unresolved identity
decisions, reviewer changes, duplicate proposals, HTTP calls, model tokens and
elapsed time. Require exact source/quote invariants on fixtures. Report semantic
quality and remaining uncertainty honestly; do not substitute a model confidence
score or mocked tests for real extraction quality or live Kanka verification.
