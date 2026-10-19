# Kanka LoreMaster

A read-only Python MCP server for campaign lore hosted on Kanka. It discovers
campaigns, resolves entity identities, reads descriptions and posts, and retrieves
inventory and explicit relationships. A full-entity tool combines supported direct
attachments with per-section provenance. Python 3.13 or newer is required.

The current MVP uses local **stdio** transport. All Kanka requests are GETs;
there are no creation, editing, transfer, or deletion tools.

## Install

Install the merged read-only MVP from `main`:

```bash
git clone https://github.com/Dfupa/kanka-mcp-loremaster.git
cd kanka-mcp-loremaster
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Credentials and campaign scope

Create a personal token in [Kanka's API settings](https://app.kanka.io/settings/api).
Provide it through `KANKA_API_TOKEN`, using your MCP client's secret configuration
or an environment variable. For a local Bash session, hidden input avoids placing
the credential in shell history:

```bash
read -rsp 'Kanka API token: ' KANKA_API_TOKEN
export KANKA_API_TOKEN
export KANKA_ALLOWED_CAMPAIGN_IDS='123'
```

Replace `123` with your campaign ID, or use comma-separated IDs. If the allowlist
is empty or unset, the server permits campaigns accessible to the token. Campaign
IDs remain explicit tool arguments; the server never guesses an active campaign.
The server does **not** automatically load `.env` files.

The token's Kanka permissions determine which records can be read. An allowlist
restricts campaigns, not audiences within a campaign. Do not share a GM-scoped
server with players: this MVP has no separate GM/player permission layer or
multi-user authentication. Never commit credentials, lore dumps, or live
responses to this public repository.

## Connect an MCP client

The executable is `kanka-loremaster`, or equivalently
`python -m loremaster.server`. Your MCP client launches it and communicates over
stdin/stdout; diagnostics use stderr.

For clients supporting the `mcpServers` JSON format:

```json
{
  "mcpServers": {
    "kanka-loremaster": {
      "command": "/absolute/path/kanka-mcp-loremaster/.venv/bin/kanka-loremaster",
      "env": {
        "KANKA_ALLOWED_CAMPAIGN_IDS": "123"
      }
    }
  }
}
```

Configure `KANKA_API_TOKEN` through the host's supported secret/environment
mechanism as well. Environment inheritance varies between clients; exporting a
variable in a terminal does not guarantee that a separately launched desktop
client passes it to the server. The example intentionally contains no credential.

The bundled smoke check is also a working MCP client: it explicitly forwards
environment credentials and launches a fresh server process over stdio.

## Tools

| Tool | Arguments | Behavior |
| --- | --- | --- |
| `list_campaigns` | none | All token-visible campaigns permitted by the allowlist |
| `find_entities_by_name` | `campaign_id`, `name` | Complete name search, with exact and partial matches separated |
| `search_entities` | `campaign_id`, `query`, optional `page` | Search candidates with verified universal IDs; completeness may be unknown |
| `get_entity` | `campaign_id`, `entity_id` | Generic metadata and verified module-specific details |
| `get_entity_full` | `campaign_id`, `entity_id`, optional `sections`, `section_pages` | Core details and all requested direct attachments, with independent section status and continuation |
| `get_entity_posts` | `campaign_id`, `entity_id`, optional `page` | One page of posts |
| `get_entity_inventory` | `campaign_id`, `entity_id`, optional `page` | One inventory page, including quantities, equipment state, positions and linked item names |
| `get_entity_relations` | `campaign_id`, `entity_id`, optional `page` | One relation page, preserving labels, direction, duplicate edges and related names |

Pages start at 1. Use the **universal entity ID**, not a character or item module
ID. Name and search inputs are limited to 1000 characters. Resolve names with
`find_entities_by_name`; if several exact matches remain,
ask the user to select a candidate. Actual entity records determine identity;
article mentions do not.

A typical interaction is:

1. Call `list_campaigns` and select a campaign.
2. Call `find_entities_by_name` with the desired character's name.
3. Use the chosen candidate's `entity_id` with `get_entity`,
   `get_entity_inventory`, or `get_entity_relations`.
4. Follow `next_page` until it is null, checking errors and missing data on every
   response. Cite the returned sources when answering.

Names, relationship labels, descriptions and HTML remain in their source
language. Natural-language answers can be translated by the calling assistant.
Inventory custom names take precedence over linked item names. Relation labels
describe recorded directed edges; the server does not infer reciprocity or family
ties from their wording.

### Full entity reads

`get_entity_full` includes core metadata and module-specific fields (including
description, traits and available dates), plus these supported sections:
`posts`, `inventory`, `relations`, `attributes`, `reminders`, `assets`, `abilities`,
`tags`, `mentions`, `permissions` and `images`. Assets include recorded aliases,
links and file metadata. Images are portrait/header references; binary files and
linked entities' complete lore are not downloaded recursively. Mentions remain
separate from recorded relations. Fantasy dates and calendar IDs are preserved
verbatim; calendar rules and date conversions are not inferred.

Omit `sections` to request all supported sections, supply a list to narrow the
read, or use `[]` for core only. Each entry in `data.sections` reports `data`,
`source`, `page`, `next_page`, `complete`, `missing_data` and `status` (`ok`,
`partial`, `unavailable` or `deferred`). A section denied with HTTP 403 or missing
with HTTP 404 has `data: null`; it is never presented as empty. Other API failures
and invalid identities fail the entire tool call. `not_requested` names excluded
sections; top-level completeness applies to the requested scope.

The aggregate reads `/profile` to discover `rate_limit` and `default_pagination`,
without returning personal profile fields. It spaces its GET attempts using the
reported quota, including retries, and accepts collection sizes according to
`meta.per_page`. There is no hardcoded subscription-tier table or requested page
size. Account pagination settings and effective API page sizes can differ; a live
profile may report `0` for an unset preference, which is preserved without using
it as the page size. See
[Kanka's profile](https://app.kanka.io/api-docs/1.0/profile) and
[pagination contracts](https://app.kanka.io/api-docs/1.0/misc/pagination).

Local safety budgets are independent of account entitlements: the aggregate
allows 100 GET attempts and 90 seconds for paced reads, within the existing
120-second tool timeout, and at most 20 collection pages per section per call.
Existing inventory/relation enrichment safeguards remain in effect. Missing
account quota metadata produces an error rather than guessing a subscription.
Other applications/tokens share the upstream quota; 429 handling still applies.

When a budget stops a section, `data.continuation` contains its next page. For
example, given `{"posts": 21, "assets": 1}`, call:

```json
{
  "campaign_id": 123,
  "entity_id": 456,
  "sections": ["posts", "assets"],
  "section_pages": {"posts": 21, "assets": 1}
}
```

Combine retrieved section pages while checking every missing-data marker. A
response starting after page 1 remains incomplete on its own. Continuation does
not fix unavailable records, missing pagination metadata or enrichment limits.
Core data is re-read on each call; this is not a transactional snapshot or an
update payload. Narrow sections when an aggregate exceeds the response-size cap.

## Result contract

Every successful tool response supplies the same object in MCP `structuredContent`
and as JSON text. This synthetic example represents an empty, complete inventory:

```json
{
  "data": [],
  "campaign_id": 123,
  "entity_id": 456,
  "source": "https://api.kanka.io/1.0/campaigns/123/entities/456/inventory?page=1",
  "complete": true,
  "page": 1,
  "next_page": null,
  "missing_data": [],
  "error": null
}
```

- `complete` applies to this response: all primary collection pages and expected
  linked data must be included. A response starting on page 2 is not a complete
  collection, even when it is the last page. A caller may combine all pages after
  checking each one. Completeness never implies access to records hidden by Kanka.
- `next_page` concerns the primary collection. A null value does not establish
  completeness when linked data is missing or pagination is unavailable.
- `missing_data` contains endpoint/reason pairs: `unavailable` for inaccessible or
  deleted links, `lookup_limit` for bounded enrichment, `unsupported_module`,
  `missing_item_name`, or `pagination_unavailable`.
- API and runtime input failures return `data: null`, `complete: false`, a
  sanitized `error` object (`code`, `message`, `status_code`), and MCP
  `isError: true`. Invalid tool arguments and unknown tool names are rejected by
  the SDK before execution and may have only the SDK's error content.

Current Kanka search responses have no pagination metadata. Their candidates are
returned with `complete: false` and `pagination_unavailable`; an empty search
response is not evidence of absence. Use `find_entities_by_name` for complete
name-based resolution. Search IDs and child IDs are normalized against verified
generic records rather than trusting the search response's child ID.

## Verification

The offline suite uses synthetic HTTP responses and dummy tokens. It includes a
real subprocess stdio MCP session covering initialization, all eight tools,
structured schemas, errors, recovery, campaign boundaries and HTTP cleanup.

Run the required checks in order:

```bash
python -m pip install -e '.[dev]'
ruff format .
ruff check .
ruff format --check .
pytest -q
```

For the authorized live campaign demonstrations, set the environment variables
above and run:

```bash
python scripts/verify_mcp.py
```

This check discovers campaigns and resolves the three demonstration names
configured in the script, then reads descriptions, inventory, relations, search
and posts through the actual stdio server. It prints fixed pass/fail diagnostics,
never lore, names, IDs or credentials. Missing links, ambiguous identities and
unexpected pagination cause failure. The explicit troubleshooting campaign in
the smoke scripts is not a server default or an authorization bypass.
The aggregate smoke check requests the posts section only; it does not establish
live correctness of every attachment endpoint. A separate GET-only stdio check
retrieved all eleven sections for one authorized entity: nine sections completed,
while attributes and permissions returned no pagination metadata and were marked
incomplete. This observation does not establish behavior for every entity,
permission set, account or campaign.

The optional GitHub live workflow is manually dispatched and restricted to the
development branch. GitHub requires its workflow file to exist on the default
branch before dispatch is available. Normal CI never uses a Kanka token or makes
live campaign requests.

### Offline creation rehearsal

The MCP server remains read-only. A separate, network-isolated script rehearses
creating one synthetic character with a description, an outgoing relation to an
existing synthetic entity, equipped inventory linked to a synthetic item, and
two private test posts. Review its ordered request preview:

```bash
python scripts/offline_creation.py
```

Copy the returned `content_digest`, then run:

```bash
python scripts/offline_creation.py --approve YOUR_PREVIEW_DIGEST
```

Both HTTP clients use an in-memory `httpx.MockTransport`; the script has no live
transport option and never reads an API token. The CLI acts as the trusted
reviewer: it validates the reviewed checksum, obtains a backend-issued grant and
claims the server-stored draft. It then resolves universal versus module IDs,
executes five mocked POSTs, and reads back through `get_entity_full` to compare
stored fields to the draft. Exiting discards synthetic data; no cleanup writes occur.

The shared `loremaster.creation` module separates a content checksum from an
execution capability. Its `ApprovalGate` denies campaigns and operation classes
unless explicitly permitted. Grants are random, bound to a stored proposal,
single-use, revocable and valid only until the proposal's deadline (five minutes
by default). Current scope and expiry are checked at approval, claim and before
each simulated POST. A preview never contains an execution token. The module has
no network executor and its approval methods are not exposed through MCP.

Python callers must migrate from `run(draft, approval_digest)` to
`prepare(draft)` → trusted-host `gate.approve(...)` → `run(grant)`. Approval audit
metadata identifies the proposal, content checksum and reviewer. The host must
authenticate that reviewer; an `approved_by` string is audit data, not login.

Tests exercise invalid drafts/references, duplicate names, rejected writes,
partial completion, concurrent claims, expiry, revocation, scope changes and
same-instance retries without duplicates. Resuming a rejected step requires a
new proposal/grant and verifies completed data before further writes. Changed
completed data, lost/malformed responses, cancellation during send, unexpected
redirects and ambiguous HTTP failures block continuation pending reconciliation.
By default the progress journal exists only within a rehearsal instance. Python
callers can explicitly supply a private `CreationJournal` and stable execution ID
to rehearse recovery across executor restarts. Intent is committed before each
send; confirmed IDs are restored, while uncertain steps remain blocked until
reviewed reconciliation. These are offline recovery checks, not production
authorization or evidence of a deployed write service.
Inventory visibility parameters require live contract verification before a
real adapter is implemented (the API docs contain a misspelled parameter).
See [the supervised-creation contract](docs/supervised-creation.md) for the trust
boundary and remaining recovery work.
The [synthetic acceptance workflow](docs/synthetic-creation-validation.md) is now
covered by SDK dispatch and a real stdio MCP session reading the created fixture.
Run `pytest -q -rP tests/test_creation_server.py` to see the synthetic
expected/observed report. Tests also cover restart recovery and explicit
reconciliation in `tests/test_progress.py`. Test servers are development-only,
excluded from the installed package; no permanent synthetic MCP tools are added.
The user's separate manual creation check was accepted and its records removed
by the user. That one-off live script is not included in this project.

## Limits and next milestones

- Complete campaign/name listings stop after 20 pages. Page sizes use the API's
  `meta.per_page`; legacy responses without that field retain a 100-row safety
  guard. Missing or inconsistent pagination causes an error, or an explicit
  unknown-completeness marker for newly supported attachment collections.
- Inventory and relations resolve at most 20 distinct linked records per call.
  Remaining edges/items stay visible with explicit missing-data markers. Search
  rejects more than 20 candidates; narrow the query or use the name resolver.
- Each GET has a 15-second timeout. HTTP 429 receives at most two backoff retries;
  `Retry-After` supports both seconds and HTTP dates. Delays over 30 seconds are
  reported to the caller for a later retry. Each MCP read has a 120-second total
  time budget. There is no persistent cache or local name-to-ID dictionary.
- Tool JSON payloads above 256 KiB return `result_too_large` rather than being
  silently truncated. Large articles may exceed this MVP limit.
- Reads are not a transaction: records can change between requests. Linked records
  are checked for identity and campaign consistency, but a campaign-wide snapshot
  is not provided.
- Unknown custom modules return generic metadata with an explicit missing-details
  marker. Calendar rules, chronology, canon proposals, session-note
  processing, remote hosting and mutation governance remain later milestones.
- The installed SDK stdio connection has been verified. Individual desktop-client
  setup and UI behavior require checking in the chosen host.

MIT licensed. See [LICENSE](LICENSE).
