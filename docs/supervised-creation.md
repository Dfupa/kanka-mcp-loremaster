# Creating entities

Writes are disabled by default. LoreMaster first saves a proposed change locally;
you review it in Terminal before it can be sent to Kanka.

## Enable the operations you need

Create a private directory outside Git. Keep the approval database: it contains
proposed lore, approvals and information needed to recover interrupted writes.

```bash
mkdir -p "$HOME/.local/share/kanka-mcp-loremaster"
chmod 700 "$HOME/.local/share/kanka-mcp-loremaster"
export KANKA_CREATION_DATABASE="$HOME/.local/share/kanka-mcp-loremaster/changes.sqlite3"
export KANKA_WRITE_CAMPAIGN_IDS='123'
export KANKA_WRITE_OPERATIONS='character.create,post.create'
```

All three settings are required together. Write campaigns must be within
`KANKA_ALLOWED_CAMPAIGN_IDS` if that restriction is configured. Pass the settings
and `KANKA_API_TOKEN` to the server through your MCP client, then restart it.
Exporting them in Terminal does not update an already running server.

| Creation | Preparation tool | Required operation |
| --- | --- | --- |
| Character | `prepare_character_creation` | `character.create` |
| Organization | `prepare_organisation_creation` | `organisation.create` |
| Organization member | Part of the organization draft | `organisation.member.add` |
| Item | `prepare_item_creation` | `item.create` |
| Location | `prepare_location_creation` | `location.create` |
| Family, quest, map, journal, note | See [entity fields](additional-entities.md) | Corresponding `TYPE.create` |
| Post on an existing entity | `prepare_entity_post_creation` | `post.create` |
| Character relationship | Part of the character draft | `relation.create` |
| Character inventory entry | Part of the character draft | `inventory.add` |

A proposal needs permission for every part. A visible tool is not permission to
execute it. Creating an item does not add it to an inventory; an organization
membership is different from a relationship.

## Prepare, review and execute

Ask your assistant to prepare the campaign, fields and visibility you intend.
For example, `prepare_character_creation` accepts:

```json
{
  "campaign_id": 123,
  "name": "Example character",
  "entry": "<p>A description you have reviewed.</p>",
  "is_private": false,
  "age": "28",
  "sex": "Male"
}
```

The tool checks names and references, saves the proposal and returns a
`proposal_id`. It does not create the record yet. Review and approve that ID
in Terminal using the same database as the server:

```bash
kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" review PROPOSAL_ID
kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" approve PROPOSAL_ID
```

Check the fields, references and privacy, then type the exact confirmation phrase
shown. Approval lasts five minutes and is consumed by execution. A chat reply or
copied digest is not approval. These commands need no API token; anyone with
access to the local database must be trusted. The recorded OS username is not
independent GM authentication.

Ask the assistant to call `execute_creation` with that campaign and proposal ID.
The server rechecks approval, creates the record and reads it back. Inspect
`get_creation_status` for progress. Dependent records need their own proposals
after prerequisites complete and their IDs are verified.

## Fields and privacy

Characters support name, HTML `entry`, text `age`/`sex`, species
`race_entity_ids`, residences `location_entity_ids`, posts, outgoing relationships
and linked inventory. References use universal entity IDs; the server resolves
Kanka's type-specific IDs. Organizations support optional type and character
memberships. Items support optional text `type`, `price`, `size` and `weight`.
The MCP schemas describe the exact objects. Other types are in
[entity fields](additional-entities.md).

New core records default to private. Posts and relationships default to visibility
3 (admins and creator). Request public records/posts explicitly. Inventory
visibility uses Kanka's default and is not configurable here.

The default role is non-admin. Private core/member operations and visibility-2
attachments are blocked locally. For an actual admin account, the host may set
`KANKA_ADMIN_CAMPAIGN_IDS` within the read allowlist. This grants no Kanka
permissions. Never make private material public to bypass an access error;
use [public-copy review](non-admin-publication.md) when publication is intended.

## If execution fails

Inspect status first. Completed steps are kept; there is no automatic rollback.
A known rejection can require fresh approval. A timeout or lost/malformed
response may mean Kanka received the write even if no result is visible.
An **uncertain outcome blocks resending**. Do not create a replacement proposal
or database, or treat an empty search as proof nothing happened.

Stop other write workers and establish which record, if any, came from the
request. The local recovery command reads that candidate and asks for confirmation:

```bash
kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" \
  reconcile PROPOSAL_ID --candidate-id RECORD_ID
```

Core candidates use universal IDs; posts, inventory, relationships and memberships
use their own record IDs. Matching fields alone do not prove origin. If evidence
is insufficient, leave the outcome unresolved. Recovery sends no new write and
grants no approval. To prevent subsequent sends:

```bash
kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" revoke PROPOSAL_ID
```

Revocation cannot retract a sent request. Reads and writes are separate API calls;
another client can change a record between them.
