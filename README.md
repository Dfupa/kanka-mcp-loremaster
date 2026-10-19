# Kanka LoreMaster

A read-only Python MCP server for campaign lore hosted on Kanka. It discovers
campaigns, resolves entity identities, reads descriptions and posts, and retrieves
inventory and explicit relationships. Python 3.13 or newer is required.

The current MVP uses local **stdio** transport. All Kanka requests are GETs;
there are no creation, editing, transfer, or deletion tools.

## Install

The implementation currently lives on the development branch:

```bash
git clone --branch feat/read-only-foundation https://github.com/Dfupa/kanka-mcp-loremaster.git
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
real subprocess stdio MCP session covering initialization, all seven tools,
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

The optional GitHub live workflow is manually dispatched and restricted to the
development branch. GitHub requires its workflow file to exist on the default
branch before dispatch is available. Normal CI never uses a Kanka token or makes
live campaign requests.

## Limits and next milestones

- Complete campaign/name listings stop after 20 pages. Individual pages are capped
  at 100 rows. Missing or inconsistent pagination causes an error.
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
  marker. Calendar rules, properties, chronology, canon proposals, session-note
  processing, remote hosting and mutation governance remain later milestones.
- The installed SDK stdio connection has been verified. Individual desktop-client
  setup and UI behavior require checking in the chosen host.

MIT licensed. See [LICENSE](LICENSE).
