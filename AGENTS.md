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
- **Active development branch:** `feat/supervised-creation`, created from merged
  `origin/main` at `4f2524c9768a5681f704e1658500dbf7e66dd7f1`.
- **Completed review:** https://github.com/Dfupa/kanka-mcp-loremaster/pull/1
  was reviewed and merged by the user; the old remote development branch was
  deleted. Do not recreate it or add work to that completed PR.
- The current slice is the shared proposal/approval contract and offline
  supervised-creation verification. Keep the next PR focused and in draft for
  review; merging and live write authorization remain separate decisions.
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
Offline creation rehearsals and development of their validated proposals are
authorized. PR acceptance and the next branch plan do **not** authorize live
Kanka writes, image uploads, or exposure of mutation MCP tools.

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

## 3. Verified implementation and merge handoff (2026-10-19)

Read the current remote state again at the start: this is a snapshot, not a
claim that files or CI results will remain unchanged.

- **Verified implementation baseline:**
  `d25e116b133f2a4174e76520657c81109793ee4d`
  (`feat: aggregate entity lore and rehearse creation offline`). This handoff is
  a documentation follow-up; inspect CI for its eventual exact commit separately.
- **Baseline CI is GREEN:**
  https://github.com/Dfupa/kanka-mcp-loremaster/actions/runs/39260169288
  completed successfully for that exact implementation commit. Ruff lint and
  format checks passed; **250 tests passed in CI** and locally. Earlier Ruff/CI
  failures and the old inconclusive identity lookup have been resolved.
- `src/loremaster/kanka.py`: asynchronous GET-only HTTPX client, campaign
  allowlists, scoped identity checks, pagination, bounded 429 backoff, and
  inventory/relation enrichment. Universal and module IDs remain distinct.
- `src/loremaster/aggregate.py`: `get_entity_full` composes verified generic and
  module fields plus eleven supported direct attachment sections: `posts`,
  `inventory`, `relations`, `attributes`, `reminders`, `assets`, `abilities`,
  `tags`, `mentions`, `permissions`, and `images`. It supports section selection,
  independent source/status/completeness, and stateless page continuation.
- `src/loremaster/server.py`: **eight read-only FastMCP tools**:
  `list_campaigns`, `find_entities_by_name`, `search_entities`, `get_entity`,
  `get_entity_full`, `get_entity_posts`, `get_entity_inventory`,
  `get_entity_relations`. `src/loremaster/results.py` defines their shared result
  envelope. All tools have read-only annotations and structured error handling.
- **Live read-only evidence:** actual SDK stdio client/server checks verified
  Marcian's identity/description, Roderick's inventory, Elgith's explicit
  relations, search and posts. A separate full-entity stdio check retrieved all
  eleven sections for one authorized entity; nine sections completed, while
  attributes and permissions lacked pagination metadata and were correctly
  marked incomplete. Account quota discovery and shutdown passed. This is not
  evidence for every account, campaign, record or permission combination.
- `scripts/offline_creation.py`: a **network-isolated rehearsal**, not a live
  writer. It validates immutable synthetic proposals, previews ordered requests,
  binds approval to a digest, resolves references, simulates a character with
  description, outgoing relation, equipped linked inventory and exactly two
  private posts, and compares read-back through the aggregate to the proposal.
  Both HTTP clients require in-memory `MockTransport`; there is no live transport
  option or token/environment loading. The CLI preview and approved execution
  passed. Tests cover rejected steps, partial progress, same-instance retries
  without duplicates, uncertain outcomes and content mismatches.
- The rehearsal journal is **in memory only**. Timeouts/5xx block blind retry
  pending reconciliation. Durable idempotency, production approval enforcement,
  transaction guarantees, live creation/editing/deletion and uploads are not
  implemented or verified. No real Kanka record has been changed.
- Tests include synthetic API boundary cases and an actual subprocess SDK stdio
  session dispatching all eight tools, output schemas, errors, recovery and
  cleanup. `.github/workflows/tests.yml` runs offline checks without credentials.
- `scripts/verify_mcp.py` is the safe GET-only stdio smoke check. Its aggregate
  demonstration requests posts only; it does not reproduce the separate full
  eleven-section live inspection. `scripts/verify_character_lookup.py` remains
  a safe direct-client identity verifier.
- `.github/workflows/verify-character-lookup.yml` is now **manual dispatch only**,
  uses `scripts/verify_mcp.py`, and is restricted to the repository and
  `feat/supervised-creation`. Its branch guard was updated from the retired
  branch in this slice. Do not remove secret protections or add live checks to
  untrusted PRs.
  GitHub dispatch also requires the workflow to exist on the default branch.
- GitHub Actions secret `KANKA_API_TOKEN` was configured earlier by the user;
  its current presence/value is not verified by these offline jobs. Check only
  inside an authorized runner when needed, and never expose it.
- `scripts/private_kanka_check.py` remains a **legacy experimental script**,
  retained and unused. Never execute it automatically or treat it as the
  canonical client/verifier. Do not commit credentials or live campaign dumps.
- Local stdio is verified; a selected desktop host's configuration/UI, remote
  hosting, multi-user authentication and separate GM/player isolation are not.

**Active slice after PR #1:** `src/loremaster/creation.py` now owns the reusable
drafts, previews, campaign/operation scope and process-local approval gate. A
preview's `content_digest` is a checksum, not permission to execute. Only a trusted
host may issue an opaque grant through `approve`; these methods are not MCP
tools or an authenticated user interface. Grants are expiring, revocable and
single-use, and the rehearsal checks current scope before each simulated POST.
The CLI is the trusted caller in the network-isolated demonstration. Known
rejections require a fresh grant to resume, and completed data is checked before
continuation. Read `docs/supervised-creation.md` for the trust/recovery contract.

PR #1's baseline evidence above remains historical. Inspect the current branch's
exact commit and tests for new changes. The production MCP still has eight
read-only tools. Durable recovery, host authentication and real execution remain
unimplemented; live writes need separate consent and a disposable campaign.

### Account behavior, completeness and remaining local safeguards

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

**Current phase:** Kanka READ-ONLY; only HTTP `GET` (or `HEAD` for verified
endpoints). Do not expose mutation MCP tools or run integration tests against
real Kanka that write anything until the user specifically authorizes a later
phase and a safe disposable/test campaign has been selected.

The accepted offline rehearsal may simulate POSTs only through its mandatory
`MockTransport`. Keep it isolated from the production GET-only client. Developing
the next proposal/approval boundary and offline tests does not enable real writes.
Do not treat a rehearsal approval digest as production authorization or its
same-instance journal as durable retry protection.

**Future user-configurable modes (planned, not active):**

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

### Next agreed slice — Supervised creation, offline first

This slice may precede broader canon-aware lorekeeping; implementing every
calendar/chronology capability is not a prerequisite for the narrow creation test.

1. The merge/branch transition is complete. Continue on `feat/supervised-creation`
   and preserve the user's untracked handoff copy without adding or overwriting it.
2. Reuse the validated proposal and read-back contracts. Design a backend
   boundary for explicit approval of exact content, campaign and operation class.
   Keep real mutation tools/execution disabled under the current authorization.
3. Extend offline coverage for reference resolution, privacy, duplicate names,
   stale state, rejected/partially completed steps, durable progress design,
   uncertain-response reconciliation and verification. Do not blindly retry POSTs
   or promise atomic transactions without a verified upstream contract.
4. Use the acceptance scenario already rehearsed: one test character with a
   description, outgoing relationship, linked inventory and exactly two posts.
   Verify universal versus module IDs and every approved field after execution.
   The inventory visibility parameter spelling in Kanka's docs is unresolved;
   verify its actual contract before implementing a live adapter.
5. Only after new write authorization and explicit selection of a disposable
   campaign, implement/enable the scoped live execution path and run that exact
   reviewed scenario. Scope cleanup explicitly; never add automatic deletion.

Image attachment is later work: documentation provides an entity image-upload
endpoint and ChatGPT file inputs, but no live upload or generated-image handoff
has been verified. Images also require separate write authorization. Do not
promise an automatic ChatGPT-generated-image-to-Kanka path based on metadata reads.

## 9. Continuity and response protocol

At the start of a Codex session, inspect `git status`, `git branch -a`, the
latest commits, the PR, `pyproject.toml`, source, tests and workflow logs.
Use `feat/supervised-creation`, based on the merged remote `main`; never push
directly to `main` without an explicit request. Keep commits focused and PRs
reviewable. Respect uncommitted changes by others, and refresh branch-dependent
README/workflow instructions deliberately. The live manual workflow's branch
guard now targets this branch while keeping its repository/secret restrictions.

When reporting outcomes in this development chat, answer in **English**, mention
changed paths/commit/PR, show actual commands and test outcomes, and distinguish
**offline mocked success**, **GitHub CI green**, and **live Kanka verified**.
If blocked, give the specific error and a realistic next step; do not claim
future work has already been carried out.

**Continuation suggestion:** "Read AGENTS.md and docs/supervised-creation.md on
feat/supervised-creation. Inspect the shared approval contract, offline rehearsal,
tests and current PR/CI before extending recovery or host integration. Keep live
Kanka read-only until new write consent and a disposable campaign are supplied.
Preserve secrets and existing user files."
