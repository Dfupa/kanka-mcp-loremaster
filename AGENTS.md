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
- **Default branch:** `main`; currently the stable skeleton (`README.md`, `LICENSE`).
- **Current development branch:** `feat/read-only-foundation`.
- **Current review:** https://github.com/Dfupa/kanka-mcp-loremaster/pull/1
  (draft PR targeting `main`; do not merge without the user's approval).
- Continue from the latest remote branch, not an earlier chat-generated ZIP.
- Keep `README.md` unchanged until the integration is functionally verified;
  then replace the placeholder with honest setup, usage and limitations.
- User-facing discussion defaults to **Spanish**. Python names, code, comments,
  docstrings, MCP descriptions, policies and skills default to **English**.
  Adapt natural-language replies to the interlocutor's language.

**Scope of standing authorization:** code changes and tests in the development
branch are allowed. The user's authorization for Kanka has been **READ-ONLY**.
There is no standing authorization to create, modify, delete, transform, or
otherwise persist anything in any Kanka campaign.

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

## 3. Actual repository state at handoff (2026-10-19)

Read the current remote state again at the start: this is a snapshot, not a
claim that files or CI results will remain unchanged.

- At inspection, the tip of `feat/read-only-foundation` was
  `77a48114fb10dbc106ccc0459e5de45aa4bdecfa`.
- `src/loremaster/kanka.py` contains an asynchronous HTTPX read-only client
  for campaigns, generic entities, posts and name-based entity resolution.
- `src/loremaster/server.py` registers MCP tools via `FastMCP`.
- `tests/test_kanka.py` contains mocked API tests.
- `.github/workflows/tests.yml` checks Ruff and Pytest.
- `.github/workflows/verify-character-lookup.yml` contains a one-time-style,
  GET-only real API check triggered by changes to that workflow file.
- `scripts/private_kanka_check.py` is a **legacy experimental script** from an
  earlier attempt. Do not treat it as the canonical API client or run it
  automatically; assess removing it once a safe replacement is validated.
- GitHub Actions secret `KANKA_API_TOKEN` was configured by the user; its
  presence/value must be checked *within the runner* if needed, never exposed.
- **CI is RED**: the two most recent Python-check jobs passed `ruff check .`
  but failed `ruff format --check .` in these files:
  `scripts/private_kanka_check.py`, `src/loremaster/kanka.py`,
  `tests/test_kanka.py`. The formatter proposals are in the job logs.
  No following `pytest` step ran on those jobs.
- The real identity check discovered **one token-visible campaign**, yet did
  **not** verify Marcian, Roderick or Elgith; this is inconclusive. The current
  verifier only tries campaign `33742` if that ID is not in the visible list.
  Inspect whether the visible campaign is already `33742`, what Kanka's
  filtered endpoint actually returns, and whether the schema assumptions
  are wrong. Do not claim the characters do not exist.
- Previous local/offline tests passed with mocked responses; **that is not
  evidence of successful live entity resolution**.

**Immediate priority:** make CI genuinely green, inspect the live read-only
lookup failure, validate the real entity schema and campaign fallback, and
make Marcian, Roderick and Elgith resolvable without a local ID dictionary.
Only then expand read-only features.

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

## 5. Core product capabilities and suggested skills

Implement iteratively; the following are **product requirements / planned
skills**, not a claim these files already exist. Keep capability contracts
separable but avoid creating an empty framework for nine skills at once.

### 5.1 Language and campaign context

`language-context`: adapt final natural-language answers to the user; default
Spanish. Discover available campaigns, identify active campaign from explicit
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

### Phase A — Repair and validate foundation (FIRST)

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

### Phase B — Read-only lore coverage

- Expand MVP to relations and inventory, followed by attributes, posts,
  calendar/events and direct cross-entity references.
- Test against mocked schemas and, only with authorization, GET-only Kanka
  responses. Preserve completeness and provenance.
- Deliver the three canonical demonstrations: **Marcian entity lookup**,
  **Roderick's inventory**, **Elgith's relations**.

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

## 9. Continuity and response protocol

At the start of a Codex session, inspect `git status`, `git branch -a`, the
latest commits, the PR, `pyproject.toml`, source, tests and workflow logs.
Prefer an incremental change on `feat/read-only-foundation` or a feature branch
based on it; never push to `main` without an explicit request. Keep commits
focused and the PR reviewable. Respect uncommitted changes by others.

When reporting outcomes, give the user's requested answer in Spanish, mention
changed paths/commit/PR, show actual commands and test outcomes, and distinguish
**offline mocked success**, **GitHub CI green**, and **live Kanka verified**.
If blocked, give the specific error and a realistic next step; do not claim
future work has already been carried out.

**First Codex assignment suggestion:** "Inspect the current remote branch and
last failed Actions logs. Make Ruff format/check and Pytest green with minimal
changes. Then diagnose and fix the strictly GET-only Marcian/Roderick/Elgith
identity lookup, trying visible campaigns first and campaign 33742 when
needed. Preserve secret confidentiality and do not modify any Kanka record."