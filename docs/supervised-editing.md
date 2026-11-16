# Editing entities

An edit changes selected fields on one existing record. The server reads the
record, shows a before/after comparison and saves a proposal. It sends the PATCH
only after separate Terminal approval.

## Enable editing

Use the [write configuration](supervised-creation.md#enable-the-operations-you-need)
and add only the required `.update` permissions to `KANKA_WRITE_OPERATIONS`.
The setting `KANKA_CREATION_DATABASE` is also used for edit approvals despite its
name. Creation permission does not authorize an edit.

| Tool | Changes | Permission |
| --- | --- | --- |
| `prepare_character_edit` | `name`, `entry`, `age`, `sex`, `race_entity_ids`, `location_entity_ids`, `family_entity_ids`, `tag_entity_ids` | `character.update` |
| `prepare_species_edit` | Existing species `entry` only | `race.update` |
| `prepare_post_edit` | `name`, `entry` on a specific `post_id` | `post.update` |
| `prepare_inventory_edit` | `amount`, `is_equipped`, `position`, `item_entity_id` on an `inventory_id` | `inventory.update` |
| `prepare_relation_edit` | `relation`, `attitude`, `is_pinned`, `colour`, `target_entity_id` on an outgoing `relation_id` | `relation.update` |
| `prepare_tag_edit` | `tag_entity_id` on an `entity_tag_id` assignment | `tag.update` |

Families, quests, maps, journals and notes have separate tools and permissions;
see [entity fields](additional-entities.md). The core record's universal
`entity_id` identifies the owner. A post/inventory/relation/tag assignment ID
identifies the attachment being changed, not the referenced item or target.

## Review and apply

For example, arguments to `prepare_character_edit` can be:

```json
{
  "campaign_id": 123,
  "entity_id": 456,
  "changes": {"age": "28", "entry": "<p>Updated description.</p>"}
}
```

Omitted fields stay unchanged. Use the returned proposal ID in Terminal:

```bash
kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" --kind edit review PROPOSAL_ID
kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" --kind edit approve PROPOSAL_ID
```

After approval, ask the assistant to call `execute_edit`. Use `get_edit_status`
for progress. Approval lasts five minutes and cannot be replayed. The server
reads the record again before writing; changes since review make the proposal
stale. Prepare and review a new proposal rather than applying an old diff to
newer data. Another client can still write between the final read and PATCH.

## What an edit preserves

- Visibility stays unchanged. A private record is never made public as a fallback.
- Blank text, null changes, duplicate references, unsupported fields and no-op
  edits are rejected. An existing empty description can still be replaced.
- List fields replace the entire assignment list. `[]` clears that list; check
  the diff carefully. Singular reference clearing and text clearing are unsupported.
- Inventory quantities must be positive integers; zero does not delete an entry.
- Relationships must be edited from their actual owner. Incoming edges and
  reciprocal changes are not inferred from names or labels.
- Replacing an entity's tag assignment does not edit the shared tag definition.

Transfers, membership rewrites, deletion and image uploads are not supported.
The default non-admin policy also applies to edits: private core records and
admin-only attachments are blocked. Visibility 3 includes the creator. An admin
role declaration must match the actual Kanka account and grants no new access.

## Unknown results and recovery

A write may reach Kanka before the connection fails. Inspect `get_edit_status`
after a failure. An uncertain write blocks new edits to the same owner, including
its attachments. Do not resend, create another proposal or replace the database.

For a result whose origin can be established, stop other editing workers and run:

```bash
kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" --kind edit reconcile PROPOSAL_ID
```

This reads the stored target, checks the expected result and requires explicit
confirmation. It sends no PATCH. Matching values alone do not establish that
this request caused them; leave the outcome unresolved if evidence is insufficient.
A fresh approval after recovery permits verification reads, not another PATCH.

A confirmed response with a failed read-back also needs verification rather than
another write. Revocation prevents future sends but cannot retract an in-flight
request. There is no automatic rollback.

## Verification limits

Scoped live trials checked character fields, a post, inventory quantity, a
relationship label, a tag replacement and a species description. Whole-list
replacement/clearing and every formatting combination have not been live-verified.
Mocked tests cover additional failure and permission cases; they do not establish
upstream atomic updates or guarantees about Kanka's reciprocal relationships.
