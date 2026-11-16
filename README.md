<h1>Kanka LoreMaster <img src="logo/kanka_mcp_server_icon.png" alt="" width="48" /></h1>

<p align="center">
  <img src="logo/kanka-loremaster.png" alt="Kanka LoreMaster fantasy lore emblem" width="384" />
</p>

Kanka LoreMaster connects an AI assistant to your [Kanka](https://kanka.io)
campaign. It finds entities, reads descriptions, posts, inventory and
relationships, and helps turn session notes into proposed campaign updates.

It starts in read-only mode. Creating or editing records requires extra
configuration and approval in your Terminal for each proposed change.

## Install

Use Python 3.13 or newer on Linux or macOS:

```bash
git clone https://github.com/Dfupa/kanka-mcp-loremaster.git
cd kanka-mcp-loremaster
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For PDF notes, install `python -m pip install -e '.[pdf]'` instead.

Create a token in [Kanka's API settings](https://app.kanka.io/settings/api).
In Bash, enter it without putting it in shell history:

```bash
read -rsp 'Kanka API token: ' KANKA_API_TOKEN
export KANKA_API_TOKEN
export KANKA_ALLOWED_CAMPAIGN_IDS='123'
```

Replace `123` with your campaign ID. Multiple IDs are comma-separated. Leaving
this setting unset permits all campaigns your token can access. The server does
not load `.env` files automatically.

## Connect your assistant

Your MCP client starts `kanka-loremaster` and communicates through its standard
input and output. For clients that use `mcpServers` configuration:

```json
{
  "mcpServers": {
    "kanka-loremaster": {
      "command": "/absolute/path/kanka-mcp-loremaster/.venv/bin/kanka-loremaster",
      "env": {"KANKA_ALLOWED_CAMPAIGN_IDS": "123"}
    }
  }
}
```

Also supply `KANKA_API_TOKEN` through your client's secret or environment settings.
A desktop client may not inherit variables from your Terminal.

For ChatGPT, OpenAI's
[Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
can connect this same local server. The tunnel needs its own runtime credential
and must stay running while ChatGPT uses it. No separate LoreMaster build is
needed. Direct ChatGPT file uploads and browser approval are not part of this
version; import notes and approve changes in Terminal.

Access follows your Kanka token. Anyone using the connection can read what that
token can read. A GM connection must not be shared with players as a way to
restrict their access.

## Read your campaign

List campaigns, choose one, then search for the entity's name. If several records
match, choose the right one before reading or proposing changes.

| Tool | What it reads |
| --- | --- |
| `list_campaigns` | Available campaigns |
| `find_entities_by_name` | Exact and partial name matches |
| `search_entities` | One page of general search results |
| `get_entity` | Metadata and type-specific details |
| `get_entity_full` | Details plus selected related sections |
| `get_entity_posts` | One page of posts |
| `get_entity_inventory` | Quantities, equipment and linked items |
| `get_entity_relations` | Recorded relationships and their direction |

Tools take an explicit `campaign_id`. Entity tools use Kanka's **universal entity
ID**, not the separate character/item/module ID. Name lookup returns the relevant
IDs; callers do not need a local ID dictionary.

`get_entity_full` can include posts, inventory, relationships, attributes,
reminders, assets, abilities, tags, mentions, permissions and image references.
Omit `sections` for all supported sections, or use `[]` for core details only.
It returns file/image metadata rather than downloading files or following every
linked entity recursively.

Check `error`, `complete`, `missing_data` and `next_page` on every response.
Follow pagination before treating a collection as complete. Missing or denied
records are not empty records. General search can lack pagination information;
use `find_entities_by_name` for complete name lookup.

Full reads may return `data.continuation`, for example `{"posts": 21}`. Resume
with `sections: ["posts"]` and `section_pages: {"posts": 21}`. Each call reads
current data; it is not a fixed campaign snapshot.

## Work with notes or change records

| Task | Guide |
| --- | --- |
| Import TXT/PDF and propose campaign updates | [Session notes](docs/using-session-notes.md) |
| Enable writes, create records and approve proposals | [Creating entities](docs/supervised-creation.md) |
| Change an existing record or attachment | [Editing entities](docs/supervised-editing.md) |
| Use families, quests, maps, journals and notes | [Entity fields](docs/additional-entities.md) |
| Publish a reviewed copy of private notes | [Public copies](docs/non-admin-publication.md) |

Names, descriptions and dialogue retain their original language. The assistant
can explain results in your language. Note corrections preserve quoted dialogue
and keep suggestions separate from established campaign facts.

## Limits

- Kanka permissions still apply. The server assumes a non-admin account unless
  explicitly configured otherwise. Enabling writes does not grant Kanka access.
- Account/API quotas determine request pacing. Local time, page and output limits
  are safety limits, not subscription restrictions.
- Listings stop at 20 pages; inventory/relationship details follow at most 20
  distinct linked records per call. Incomplete results are marked explicitly.
- Tool responses are limited to 256 KiB. Full reads stop after 100 GET attempts
  or 90 seconds; the overall tool timeout is 120 seconds.
- Changes require separate approval and a read-back check. There is no automatic
  rollback. If a write's result is unknown, inspect status before doing anything
  else; do not resend it or replace the approval database.
- Deletion, transfers, image uploads, OCR, calendar interpretation and automatic
  campaign rewriting are not supported.
- Linux has been used for local validation. macOS still needs checking on a Mac.
  Mocked tests do not prove every live operation. Map creation and some collection
  edits, in particular, lack live verification.

## Development checks

The suite uses synthetic records and mocked API responses:

```bash
python -m pip install -e '.[dev]'
ruff format .
ruff check .
ruff format --check .
pytest -q
```

Do not use real credentials or campaign data in tests, commits or CI output.
The optional `scripts/verify_mcp.py` check makes real GET requests using your
token and diagnostic targets in the script. Review it before running it;
it is not part of the offline suite.

Licensed under [MIT](LICENSE).
