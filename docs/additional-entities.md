# Entity fields

Kanka journals are diaries/session logs. Notes are standalone records. Posts are
attached to another entity and appear in its Posts tab. Missions use quests.
These are different record types with separate permissions.

## Creation and editing

| Type | Creation tool | Editing tool | Additional fields |
| --- | --- | --- | --- |
| Family | `prepare_family_creation` | `prepare_family_edit` | `location_entity_ids` |
| Quest | `prepare_quest_creation` | `prepare_quest_edit` | `location_entity_ids`, `instigator_entity_id`, creation-only `parent_entity_id` |
| Map | `prepare_map_creation` | `prepare_map_edit` | `location_entity_id`, `is_real` |
| Journal | `prepare_journal_creation` | `prepare_journal_edit` | `location_entity_ids`, `author_entity_id`, `date` |
| Note | `prepare_note_creation` | `prepare_note_edit` | Creation-only `parent_entity_id` |
| Location | `prepare_location_creation` | Not supported | `title` |

These creation tools take a `draft` object containing `campaign_id`, `name`, HTML
`entry`, optional `type`, `tag_entity_ids` and fields from the table. New records
are private by default; set `is_private: false` explicitly for public records.
Maps default to `is_real: false`.

For example, `prepare_journal_creation` accepts:

```json
{
  "draft": {
    "campaign_id": 123,
    "name": "Session at the bridge",
    "entry": "<p>The group crossed safely.</p>",
    "is_private": false,
    "date": "2026-11-16"
  }
}
```

Edits take `campaign_id`, the target's universal `entity_id` and a `changes`
object. Name, description (`entry`), type and tag assignments can be edited along
with the type-specific fields above, except creation-only parents. Omitted
fields remain unchanged. Null/blank edits are rejected; arrays replace all
assignments, including clearing with `[]`. Privacy cannot be edited.

Use `family.create`/`family.update`, `quest.create`/`quest.update`, and so on.
Locations support `location.create` only. Follow the same
[creation approval](supervised-creation.md) or [edit approval](supervised-editing.md)
workflow as other records.

## References and children

All supplied references use universal entity IDs. The server checks their
campaign and type and resolves type-specific IDs where Kanka requires them.

- Quest instigators and journal authors refer to existing entities.
- A new quest can have a verified quest parent; a new note can have a note parent.
  This creates a child record, not a quest checklist element.
- Parents cannot be changed through the edit tools.
- Journal dates are literal text subject to Kanka validation. The server does not
  convert fantasy-calendar dates.

Create and verify prerequisites first, then review each dependent proposal with
its real IDs. Approval of a parent never pre-approves children.

## Posts and family members

`prepare_entity_post_creation` adds a post to an existing owner. Its `draft`
contains `campaign_id`, universal `entity_id`, `name`, HTML `entry` and optional
`visibility_id`. The default is 3 (admins and creator); public posts use 1. It
requires `post.create`. Post edits use `post.update` and an exact `post_id`.

Character `family_entity_ids` edits replace family assignments. They do not create
family-tree relationships or rewrite organization memberships.

## Limits

Map tools create/edit metadata only, without a background image, markers, layers
or uploads. Family trees, quest elements/status changes, calendar configuration,
existing parent changes, membership rewrites and deletion are unsupported.

Live trials covered location/family/quest/journal/note creation and selected
posts, child quests/notes and family assignments. Map creation remains
mock-tested only. The family/quest/map/journal/note core edit paths also have
mocked coverage rather than a complete live trial for each supported field.
