# Kanka LoreMaster — Codex project instructions

> **Canonical Codex file:** `AGENTS.md` in the repository root. This document is
> also distributed as `AGENT.md` to preserve the requested handoff filename.
> Maintain only `AGENTS.md` in Git; avoid divergent copies in the repository.

## 1. Mission, ownership and repository

Build **Kanka LoreMaster**, a Python Model Context Protocol (MCP) integration
that serves as a dependable multilingual librarian, continuity checker and,
only when authorized, curator for tabletop-RPG campaigns hosted on Kanka.io.
The system must know the difference between **reading established lore** and
**proposing changes to the campaign's canon**.

- **Canonical GitHub repository:** https://github.com/Dfupa/kanka-mcp-loremaster
  (note this exact spelling; not `kanka-loremaster-mcp`).
- **Owner:** `Dfupa`; **license:** MIT; **repository visibility:** PUBLIC.
- **Default branch:** `main`.
- **Active development branch:** `feat/supervised-item-creation`, created from
  merged `origin/main` at `0a1987d1ee42433599454686a1856d207fbfaa83`.
- **Completed reviews:** PRs #1, #2 and #3 were merged by the user. Their remote
  feature branches were deleted; do not recreate them or add work to them.
- **Current slice:** organization creation with character memberships first,
  then item proposals/execution, reusing supervised approval and durable progress.
  Keep the new PR in draft. The user explicitly requested **offline validation
  only** for this slice: no live Kanka tests, writes or credential use.
- Preserve the user's untracked `codex_kanka_loremaster_AGENTS.md` unchanged.
  Test fixtures stay outside the release package; do not add synthetic MCP tools
  or retain campaign-specific one-off helpers in the product.
- Continue from the latest remote branch, not an earlier chat-generated ZIP.
- `README.md` now documents the verified implementation, setup and limitations.
  Keep it accurate as functionality and branch setup change.
- This development chat and its handoff use **English**, as explicitly requested
  by the user. Python names, code, comments, docstrings, MCP descriptions, policies
  and skills use **English**. Product answers adapt to the interlocutor's language;
  Spanish remains the product default when no language is specified. Preserve
  source lore in its original language.

**Scope of standing authorization:** code changes and tests in the development
branch are allowed. The user's authorization for Kanka has been **READ-ONLY**.
There is no standing authorization to create, modify, delete, transform, or
otherwise persist anything in any Kanka campaign.
Offline rehearsals and development of opt-in supervised character, organization,
membership and item creation are authorized. The current request explicitly
excludes live Kanka testing. The normal server remains
read-only. Enabling its optional creation tools requires explicit local database,
campaign and operation configuration; execution also requires separate local
review/approval. This development authorization does **not** permit new live Kanka
writes, deletion, image uploads, automatic modes or unrestricted mutation tools.

## 2. Engineering philosophy: Ponytail (full mode)

Apply the **Ponytail** minimal-complete-solution approach throughout development:
https://github.com/DietrichGebert/ponytail (`skills/ponytail/SKILL.md`, MIT).
If the Ponytail skill is installed in Codex, use it; the following rules apply
regardless of installation.

1. First read the existing code, callers, tests, configuration and current diff.
   Reuse what already exists. Prefer standard-library and existing dependencies.
2. Ship the smallest *complete and correct* vertical slice. No speculative
   abstractions, premature multi-agent framework, automatic database, or
   gratuitous class hierarchies. Optimize for reliability and readability.
3. Write idiomatic, efficient Python with **PEP 8**, precise type hints,
   descriptive names, short functions, and useful docstrings documenting
   behavior, parameters, output, and failure modes.
4. The user specifically asks for **checks and assertions even for one-liners**.
   Meet that intent through boundary validation and meaningful unit-test
   assertions for small functions. **Do not use Python `assert` for runtime
   security/input validation**, because assertions can be disabled by `-O`;
   use explicit exceptions for production guards.
5. Test happy paths, unusual values, errors, pagination, Unicode, ambiguity,
   campaign scoping, permissions, confidentiality and cleanup. Mock HTTP
   without any live token in unit tests.
6. Avoid Ruff's `B023` loop-variable closure trap; prefer
   `pytest.mark.parametrize` or explicit early binding in test cases.
7. Do not weaken lint rules, skip tests, suppress failures, or declare green
   CI until the actual GitHub Actions run has completed successfully.
8. For every change, state what was checked, what was not checked, and any
   remaining risk. No claims of live functionality based only on mocks.

**Existing implementation choices:** Python >=3.13, `mcp`/FastMCP, `httpx`,
`pytest`, `pytest-asyncio`, and `ruff`; build via `hatchling`. Check
`pyproject.toml` before altering versions or bringing in dependencies.

**Required verification before each push/PR-ready claim:**

```bash
python -m pip install -e '.[dev]'
ruff format .
ruff check .
ruff format --check .
pytest -q
```

Run the commands in this order and examine the final CI run for the exact commit.
Do not equate a Ruff formatting failure with a failing test suite: GitHub's
workflow stops before `pytest` when its earlier steps fail.

## 3. Verified implementation and current development (2026-10-19)

Read current Git/remote state at session start; this is a handoff snapshot.

- **Merged baseline:** PR #3's feature tip
  `cb076ba5f847b1b38d1e8a9f2815aea1175c63fa`; main merge `0a1987d`.
  Baseline Ruff/CI passed with **416 tests**:
  https://github.com/Dfupa/kanka-mcp-loremaster/actions/runs/39291728407.
  This is historical evidence, not the CI result for subsequent changes.
- `kanka.py` remains a GET-only HTTPX reader with campaign scope, verified
  universal/module IDs, pagination, bounded 429 backoff and enrichment.
- `aggregate.py` combines core/module fields and eleven direct sections:
  posts, inventory, relations, attributes, reminders, assets, abilities, tags,
  mentions, permissions and images. Preserve provenance and incompleteness.
- The default server exposes **eight read-only tools**. Live stdio checks have
  verified the three canonical character demonstrations and a full-entity read
  for one authorized record. Nine sections completed; attributes and permissions
  lacked pagination metadata and were correctly marked incomplete. Do not extend
  this evidence to every campaign/account/record.
- `creation.py` retains immutable drafts, previews, scope checks and the
  process-local `ApprovalGate` for network-isolated rehearsals. A digest is a
  checksum, never authorization. `progress.py` supplies optional private SQLite
  intent/results, restart recovery and reviewed reconciliation. Intent commits
  before sends; uncertain outcomes never permit blind retry.
- **Scoped adapter:** `approvals.py` stores immutable proposals and
  expiring, atomic single-use local approvals in the same private SQLite file.
  `approve.py` is a separate terminal command; it cannot be called through MCP.
  Local OS file access is the trust boundary. This does not authenticate remote
  users or protect against an administrator/agent with the same filesystem access.
- `execution.py` is a separate narrow POST writer. Explicit campaign/operation
  scope, local approval, identity rechecks, durable intent, per-send guards and
  final field/ID read-back are required. It does not edit/delete, roll back,
  automatically reconcile or retry unknown outcomes. Approval is refused while
  intent is uncertain; reconciliation must precede fresh approval.
- Default startup still has eight read-only tools. Opt-in startup now has
  **fourteen tools**: the reads plus character/organization/item preparation,
  generic `execute_creation`, the compatible character-only execution alias,
  and `get_creation_status`. Every operation still needs explicit backend scope.
  Approval/revocation/reconciliation remain separate local host actions.
- The three finite draft types share preview, approval, POST transport, journal
  and verification. Existing character payloads without a type discriminator
  remain readable. New organization/item payloads bind an explicit type into
  the stored review digest. Unknown types/fields fail closed.
- Organization members use universal character IDs in proposals, then module
  IDs in `organisations/{organisation.id}/organisation_members`. Core and member
  creation have separate operation classes. Membership list reads verify owner,
  campaign, unique IDs and complete pagination; unknown completeness blocks
  verification/reconciliation. Item creation is separate from inventory addition.
- See README and `docs/supervised-creation.md` for configuration, the exact supported
  fields and limitations. No database opens in default read-only mode.
- New coverage includes actual SDK stdio sessions with a separate interactive
  approval subprocess and shared SQLite storage, mandatory mock HTTP, expiry,
  revocation, cancellation, malformed responses and all four reconciliation
  classes. A passing mock workflow is **not** a live test of this new adapter.
- `scripts/offline_creation.py` remains forcibly isolated with MockTransport and
  no token loading. `verification.py` shares strict field/ID read-back checks;
  numeric inventory quantities are accepted, API collection order is not assumed,
  and request-only `two_way` is not a live one-way guarantee.
- `.github/workflows/tests.yml` runs offline checks without credentials. The live
  verification workflow remains manually dispatched, GET-only, repository scoped
  and guarded to `feat/supervised-item-creation`. Do not remove secret protections.
- `scripts/private_kanka_check.py` is legacy, retained and unused; never run it
  automatically. No credentials, campaign dumps or live test IDs belong in Git.
- Desktop-host UI, remote hosting, multi-user authentication, inventory per-entry
  privacy configuration, upstream transactions, append-only audit retention and
  database migrations remain unverified/unimplemented as applicable.

**Subsequent authorized live acceptance:** the user reviewed draft PR #3 and
explicitly requested a public synthetic test through the new executor, expanded
with an HTML biography, text age/sex, species and residence. Optional universal
`race_entity_ids`/`location_entity_ids` now resolve verified module IDs for Kanka's
`races`/`locations` arrays and participate in preview, intent and read-back.
The normal opt-in stdio server and separate local approval command passed the live
workflow, including five confirmed steps, independent exact field/attachment
verification, replay denial, journal reopening and shutdown. Two public synthetic
dependencies were seeded separately; their creation is not an MCP capability.
An initial GET-only preflight 429 sent no character POST; after cooldown a fresh
local approval continued the same proposal. Additional read-back completed after
cooldown. Account pacing cannot reserve quota shared with other clients.
The local host performed approval under explicit user test authorization; this
does not prove independent human authentication. Test records remain for user
inspection; deletion was not requested. No live IDs, payloads or campaign-specific
helpers are committed. No new standing write permission is implied.

### Account behavior, completeness and remaining local safeguards

**One-off live exception (2026-10-19):** after the offline design, the user
explicitly requested one synthetic creation run in their selected current campaign
and then required all new records/attachments to be public. The interrupted
private attempt performed no writes. A temporary, separate direct-API script
subsequently made seven successful POSTs: three synthetic core records (character,
mentor and compass), one relation, one inventory entry and two posts. Production
MCP tools remain read-only; this is not standing authorization for future writes
or deletion. The user subsequently confirmed that the test worked and that they
deleted the synthetic records themselves. Do not recreate them to verify cleanup.

Live read-back through MCP `get_entity_full` and additional scoped GETs confirmed
the description, public core records/posts/relation/inventory, two equipped linked
compasses at Belt and distinct post IDs. Kanka also created a mirrored relation
despite `two_way: false`; the user explicitly asked to ignore this issue. Preserve
the observation without treating it as a blocker or investigating it in this slice.
Do not claim a live one-way guarantee. Inventory
read-back used numeric `amount: 2` and `visibility_id: 1`, rather than the mock's
string amount/visibility assumptions. Some create endpoints returned HTTP 201.

The temporary script passed four mocked checks and Ruff. Its token-free local
report was local only; no live payloads or new IDs were published to GitHub. The
one-off live script is internal test scaffolding, not a file to retain in the
project. Automated creation/server fixtures live under `tests/` and are excluded
from the installed package; deprecate obsolete scaffolding before the final
release, preserving useful regression tests. Manual acceptance does not
authenticate a production approval surface or enable MCP writes.

- Follow the user's decision to adapt to actual account allowances rather than
  increasing fixed limits or hardcoding free/premium tiers. Aggregate reads use
  `/profile`'s `rate_limit` for pacing and each response's `meta.per_page` for
  effective page size. A live profile returned `default_pagination: 0` for an
  unset preference; preserve it without treating it as the page size. Unknown
  quota metadata fails explicitly; do not infer entitlement from a tier name.
- Existing local safety budgets are **not subscription limits**: complete
  campaign/name listings and each aggregate section stop at 20 pages per call;
  aggregate reads allow 100 GET attempts and 90 seconds of paced reads within
  the 120-second MCP timeout. Existing linked enrichment is capped at 20 distinct
  records per call. Legacy pages without `meta.per_page` retain a 100-row guard.
  Tool JSON responses are capped at 256 KiB. Do not silently truncate or call
  these account limits. Continuation does not repair missing linked enrichment.
- The aggregate includes direct attachment data and image/file references, not
  recursive graph expansion, binary downloads, interpreted calendar rules or
  conversion of fantasy dates. Preserve dates and calendar IDs verbatim.
- `complete` applies to the requested scope in **this response**. A continuation
  starting after page 1 remains incomplete on its own; callers combine pages and
  inspect missing data. An unavailable section is not empty, a missing pagination
  contract cannot prove completeness, and multiple reads are not a snapshot.

## 4. Kanka API contracts and identity resolution

Official documentation:
- https://app.kanka.io/api-docs/1.0/entities
- https://app.kanka.io/api-docs/1.0/characters
- API base: `https://api.kanka.io/1.0/`

Verify **current** behavior from the official docs and safe response metadata
rather than guessing field names. Respect actual Kanka permissions and privacy
of the authenticated token. The user may be unable to reach `kanka.io` from
his own network; remote GitHub-hosted checks may be necessary.

**Three distinct IDs must never be conflated:**

- `campaign_id`: campaign container; used in every campaign endpoint path.
- **Universal entity ID:** the `id` returned by a generic `entities` record,
  and the `entity_id` associated with resource-specific records.
- **Module-specific record ID:** `id` in `characters`, `locations`, etc.; this
  is usually `child_id` in the corresponding generic entity response.

A character named Marcian is *not* the same as a mention of "Marcian" in an
article. Resolve actual entity records, not text mentions or guessed IDs.

**Preferred resolution algorithm (no persistent name-ID dictionary):**

1. Discover campaigns visible to the token via `GET /campaigns` and paginate
   accurately; never guess the active campaign.
2. Within the selected campaign, call
   `GET /campaigns/{campaign_id}/entities?name=<URL-encoded name>`.
   Kanka documents `name` as a SQL-like substring filter; it is **not** an
   exact-match guarantee. Optionally narrow by documented type filters.
3. Paginate completely or **fail closed** on a safety limit; an incomplete
   search is not evidence that a name is absent.
4. Compare names with Unicode normalization (NFC) and case folding, separately
   returning exact and partial matches. Respect user-entered accents and
   distinguish aliases only when supported by actual Kanka records.
5. Return all candidate `(campaign_id, entity_id, child_id, type, name)`
   combinations. If several exact matches remain, **request disambiguation**;
   never arbitrarily take the first. Avoid assuming a matching *name* makes
   the entity a *character*.
6. Confirm a selected candidate with `GET /entities/{entity_id}` and, where
   appropriate, `GET /characters/{child_id}` or another type endpoint.
   Verify campaign, IDs, entity type and identity against actual API fields.
7. Use the resolved **universal entity ID** for generic related endpoints,
   including posts, inventory and relations, once each endpoint's contract
   has been verified.
8. Read canonical current data from Kanka. An optional **in-memory** cache is
   acceptable only after profiling, with a short TTL/invalidation and no
   assumption that IDs/names never change; never require a maintained local
   JSON dictionary or full-campaign reindex to answer a simple lookup.

**Fallback campaign for troubleshooting:** `33742` is an ID explicitly
supplied by the user for **read-only testing**, not the universal default or
permission bypass. First try token-visible campaigns, then, if requested
characters were not resolved, also test `33742` when appropriate, even if it
appeared in the visible list but earlier results were inconclusive. Deduplicate
calls and never bypass Kanka's authorization responses.

Cover missing/malformed JSON, 401, 403, 404, 429, 5xx, timeouts, inconsistent
IDs, unexpected pagination, private records, duplicate names, nonexistent
campaigns, Unicode and API rate limiting. No silent partial results. Use a
bounded request budget, backoff on 429, reasonable timeouts, and avoid exposing
sensitive payloads or Bearer tokens in error messages and logs.

**Verified search-schema caveat:** the live `/search/{term}` endpoint returned
`data` and `sync` without pagination metadata. Its `id` was a universal entity ID,
while its `child_id` representation did not match the module record ID. Older
responses may supply `entity_id` separately. The client verifies candidates
against generic records and returns normalized identity fields; it marks unknown
search completeness explicitly. Continue to use `find_entities_by_name` for
complete name resolution. Do not restore assumptions based only on older docs.

## 5. Core product capabilities and suggested skills

Implement iteratively; the following are **product requirements / planned
skills**, not a claim these files already exist. Keep capability contracts
separable but avoid creating an empty framework for nine skills at once.

### 5.1 Language and campaign context

`language-context`: adapt product answers to the user; default Spanish when no
language is specified. This development conversation remains English. Discover
available campaigns, identify active campaign from explicit
user/session context, and separate player-visible from GM-only knowledge.
Never cross-contaminate campaigns. Ask for disambiguation when required.

### 5.2 Entity management

`kanka-entity-management`: list, search, resolve, read and eventually propose
creating/updating/deleting typed entities (characters, places, quests, items,
organizations, events, etc.). Support aliases, duplicates, visibility,
provenance and Kanka's universal/child-ID distinction. Creation, edits and
deletions must never be silently inferred from a discussion.

### 5.3 Relationships

`entity-relations`: read explicit entity relationships and their target IDs,
direction, status and description. Separate **actual Kanka relations** from
mentions, tags, inferred family ties, roleplaying rumors and AI guesses.
Handle duplicate targets, reciprocal or directed relations and stale links.
Any proposed edit must respect canon and require the selected write policy.

### 5.4 Inventory and equipment

`entity-inventory-management`: resolve the owner entity, then read inventory,
equipped/unequipped status, amounts, positions, item records and relevant
custom attributes as exposed by the API. Do not confuse an entity's inventory
with an item mentioned in its description or a different character's inventory.
Later support supervised transfers, additions and removals with integrity
checks and verification after a write.

### 5.5 Calendar and timekeeping

`calendar-timekeeping`: support Kanka's campaign-specific/fantasy calendars,
current in-world date, holidays, birthdays, event recurrence and date formats.
Do **not** assume Gregorian years, a fixed month length or a universal
real-world/in-world date conversion. Validate each campaign's calendar rules.

### 5.6 Chronology

`campaign-chronology`: build a consistent event timeline from Kanka event and
calendar records, including session events, relative/uncertain dates and
cause-effect links. Preserve conflicting accounts and unknown chronology.

### 5.7 Canon integrity

`canon-integrity`: compare prospective lore to the existing campaign. Label
facts as **confirmed canon**, **source-backed rumor/in-world assertion**,
**hypothesis**, **creative suggestion**, or **contradiction**. Never fabricate
missing canon or silently overwrite it. For conflicting claims, cite exact
records and ask for clarification before a persistent change.

### 5.8 Change governance

`change-governance`: implement backend-enforced modes (see next section),
version checks, idempotency, explicit preview/diff, approval and audit metadata.
The LLM's instructions alone must **never** be the authorization boundary.

### 5.9 Lorekeeping and session notes

`lorekeeping`: ingest GM/player session notes in natural language; identify
NPCs, protagonists, quests/subquests, events, inventory changes, locations,
relationships and changes in the in-world date. Cross-reference existing
records, flag ambiguities, preserve who said what and when, present a structured
proposal, then only persist items allowed by governance. Associate changes
with their originating note/session and retain provenance for reversibility.

## 6. Writes: explicit governance, never implicit consent

**Default runtime:** Kanka READ-ONLY. The user accepted developing a narrowly
scoped opt-in character/organization/item creation on the current feature branch. Enable it
only with all three explicit local creation settings and separate terminal
approval. Do not add unrestricted writes or treat chat assertions as approval.
No new live integration writes are authorized by this development request.

The accepted offline rehearsal still permits POST simulation only through its
mandatory MockTransport. The production GET client stays separate from the writer.
A preview/review digest is not authority. Protect the private SQLite file outside
Git, and do not describe same-OS file access as remote/human authentication.

**Governance modes:** the narrow opt-in supervised workflow is implemented in
this branch; broader governance and both automatic modes remain planned.

1. `supervised` (**default**): read and verify, prepare a proposal/preview,
   explain consequences, and require explicit approval before committing.
2. `auto_with_suggestions`: suggest changes and ask to include them (e.g.,
   "¿Quieres que incluya esta entidad...?"), applying only approved proposals.
3. `auto_always`: automatically perform only narrowly pre-authorized,
   non-destructive changes in explicitly scoped campaigns; never silently
   delete, overwrite canon, handle uncertain identities or change GM-private
   visibility. Dangerous/ambiguous operations still require confirmation.

Enforce both *campaign scope* and *operation class* server-side; do not rely on
an LLM deciding its own permissions. For any future write: read current state,
resolve IDs, construct a minimal diff, validate canon and access, obtain the
required confirmation, re-read/version-check to guard against races, apply an
idempotent transaction or safe operation, then read back and report the outcome.
Keep user-visible audit information, and never leak GM-private content to a
player persona. Separate suggestions from successful persisted operations.

## 7. MCP / integration design

- Prefer small independently testable layers: **HTTP client** (API transport),
  **domain resolution** (identity/canon rules), **MCP tools** (validated public
  contract), and **optional governance** (not until writes are authorized).
- Use FastMCP and the official MCP SDK already in `pyproject.toml`; start with
  `stdio` for local Codex/MCP clients. A remote transport, production hosting,
  or ChatGPT plugin packaging is a later independent milestone.
- Python backend should adapt to the selected campaign and user language while
  skills and tool schemas remain stable English contracts.
- Provide structured errors and provenance (`campaign_id`, `entity_id`, source
  endpoint, completeness, missing data), not free-form fabricated claims.
- Avoid excessive result payloads; support explicit pagination and useful
  bounded summaries for large articles, inventories and relationship graphs.
- Consider `python-kanka` only after confirming that it reduces total code
  and maintains API fidelity. A minimal HTTPX client is acceptable.
- Protect secrets and privacy: env-based `KANKA_API_TOKEN`, no hardcoded tokens,
  no network logging of Authorization, and never commit campaign dumps.
- The GitHub repo is **public**, even though the connected campaign is private.
  Treat GitHub Actions logs, test snapshots, comments, PRs and artifacts as
  potentially public. Do not print names/IDs/full responses from real
  campaigns without a private delivery channel. Do not circumvent tooling or
  safety blocks with encrypted public-log exfiltration techniques.
- GitHub Actions checks must not grant untrusted pull requests access to
  repository secrets. Prefer controlled, manually initiated read-only smoke
  checks and output limited to non-sensitive pass/fail diagnostics.

## 8. Development roadmap and acceptance criteria

### Phase A — Repair and validate foundation (completed at verified baseline)

- Inspect latest branch and failed CI logs; fix ALL Ruff check/format failures
  including the legacy script if it remains tracked. Run actual Pytest.
- Verify MCP server imports and tool registration under the installed SDK.
- Confirm pagination and name-filter semantics against official Kanka docs.
- Diagnose the live search for **Marcian**, **Roderick**, **Elgith** through
  visible campaigns and the explicitly supplied `33742` fallback, with GET only.
- Confirm matches are **entity records**, not mentions; distinguish `entity_id`
  from child/module-specific ID and flag ambiguous duplicates.
- Do not report this phase complete until **CI is green and the real read-only
  check demonstrates correct identity resolution**, or clearly document an
  external permission/API blocker.

### Phase B — Read-only lore coverage (MVP delivered; wider coverage remains)

- Expand MVP to relations and inventory, followed by attributes, posts,
  calendar/events and direct cross-entity references.
- Test against mocked schemas and, only with authorization, GET-only Kanka
  responses. Preserve completeness and provenance.
- Deliver the three canonical demonstrations: **Marcian entity lookup**,
  **Roderick's inventory**, **Elgith's relations**.
- These demonstrations, the aggregator and its account-aware behavior are
  delivered at the baseline in section 3. Calendar interpretation, chronology,
  recursive references and full permission coverage remain future work; do not
  describe the entire read-only product roadmap as finished.

### Phase C — Canon-aware lorekeeping

- Parse session notes into structured, source-backed proposals.
- Implement ambiguity/conflict detection, date disambiguation, provenance,
  GM/private handling and user-visible preview. **Still read-only** until
  mutation governance is separately approved.

### Phase D — Controlled writes and deployment (only with new consent)

- Implement governance modes incrementally and verify against a disposable
  campaign; protect against duplicate writes, partial updates and stale canon.
- Package/configure the MCP integration for the target Codex/ChatGPT client,
  document required permissions, and revise README once end-to-end verified.
- Never enable deletion or automatic canon rewriting as a side effect of
  implementing an apparently read-only feature.

### Current agreed slice — Organization membership and supervised items

1. Continue on `feat/supervised-item-creation`, based on merged PR #3. Preserve
   the untracked handoff copy. Keep commits focused and the new PR in draft.
2. Organization creation and two actual memberships (synthetic Éldan and mentor)
   were added before item creation. Both use the same local approval, scope,
   durable progress and read-back path. Validate everything offline only.
3. The complete stdio scenario creates mentor → compass item → Éldan with
   relation/inventory/two posts → organization with both characters as members.
   Each entity is its own explicitly approved proposal. Returned universal and
   module IDs bind later steps. Ten mocked POSTs; no direct setup writes, generic
   batch transaction, live IDs or automatic deletion.
4. Run install, Ruff format/check/format-check and full Pytest in order before
   pushing; inspect CI for the exact published commit. Historical live character
   acceptance does not establish live organization/member/item API behavior.
5. Full canon-aware session-note processing, broader edits, uploads, transfers,
   automatic modes and remote authentication remain future milestones. The user
   explicitly asked to ignore the observed relation mirroring as a blocker.

Image attachment remains later work. Do not promise a generated-image-to-Kanka
handoff based on metadata reads; uploads need separate authorization and tests.

## 9. Continuity and response protocol

At session start inspect `git status`, branches, latest commits, current PR,
`pyproject.toml`, source, tests and workflow logs. Work on the active feature
branch; never push directly to `main` without an explicit request. Keep commits
focused and PRs reviewable. Respect other people's uncommitted changes.

This development chat uses **English**. Report changed paths/commit/PR, actual
verification commands and results. Distinguish **offline mocked success**,
**GitHub CI green for the exact commit**, and **live Kanka verified**. State any
remaining limitation or specific blocker; never claim future work is finished.

GitHub Actions secret `KANKA_API_TOKEN` was configured earlier; these offline
checks do not verify its current presence/value. Only inspect it within an
authorized runner without exposing it. Live workflow dispatch requires its file
on the default branch; retain repository/branch guards and secret protections.

**Continuation:** read this file and `docs/supervised-creation.md`, inspect the
current draft PR/CI, and review the opt-in local approval/execution slice.
Default startup stays read-only. New live writes need separately reviewed scope
and exact proposals; no automatic cleanup is authorized. Preserve user files and
keep credentials and live campaign data out of this public repository.
