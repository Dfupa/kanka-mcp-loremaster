# Publishing a public copy of session notes

Private notes can contain GM-only information. Before publishing anything,
review a separate public copy, including its title, metadata and quoted dialogue.
The original stays in the private notes database.

“Public” means Kanka's non-private setting, subject to campaign access rules.
The server assumes a non-admin account; leave `KANKA_ADMIN_CAMPAIGN_IDS` unset
unless you actually have an admin account for that campaign.

## Prepare the copy

Use the [session-note workflow](using-session-notes.md). An operation intended
for publication needs `audience: "all"` and a `publication.rationale` explaining
why the selected material can be shared. An example operation is:

```json
{
  "key": "session-journal",
  "action": "create_entity",
  "entity_type": "journal",
  "session_key": "s12",
  "claim_keys": ["crossing"],
  "fields": {"name": "Session 12 — Crossing the bridge"},
  "audience": "all",
  "publication": {"rationale": "Publish the reviewed account without GM notes."},
  "body": [{"kind": "text", "text": "The group crossed the bridge safely."}]
}
```

Session/claim keys must come from the actual analysis. For a private source,
write the public body explicitly; `session_text: true` cannot publish the entire
account. Posts also need an explicit public title. A source label or assistant's
rationale does not authorize publication.

The server prepares non-private new records or public posts. Existing edits keep
the target's visibility; this workflow never makes an existing private record
public. Public references must also be public. Incomplete lookups, unknown
visibility, conflicting claims and other unresolved questions still block changes.

Quoted dialogue must preserve the complete protected speaker/dialogue passage.
Partial quotes are rejected rather than expanded into surrounding private text.
The reviewer must decide whether the words and metadata are appropriate to share;
there is no automatic guarantee that all secrets have been removed.

## Approve and publish

1. Read the full preview, including original text, public copy and omissions.
2. Prepare one selected operation with `prepare_session_note_handoff`. Confirm
   the concrete fields and reference IDs in its stored proposal.
3. Review and approve it in Terminal using the same approval database:

   ```bash
   kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" review PROPOSAL_ID
   kanka-loremaster-approve --database "$KANKA_CREATION_DATABASE" approve PROPOSAL_ID
   ```

   Add `--kind edit` for an edit. The command requires this exact displayed phrase:

   ```text
   publish PROPOSAL_ID REVIEW_DIGEST PUBLICATION_REVIEW_DIGEST
   ```

   Use the displayed values. Ordinary `approve ...` confirmation is insufficient
   for a public copy derived from notes.
4. Ask the assistant to execute that exact proposal with `execute_creation` or
   `execute_edit`. Approval expires after five minutes and cannot be replayed.
5. Verify the result before preparing dependent records. Each needs its own review.

The executor checks that cited public material is still available, public and
unchanged before sending. Changed evidence requires refreshed analysis/review.
The local stores keep the source and review information; only the reviewed Kanka
request is sent. These checks are separate API reads, not a transaction lock.

## Limits

Public citations currently support public core names/descriptions and public
posts on public owners. Other fields, inventory/relationships and unknown
visibility cannot be assumed safe to cite publicly. Original-session review is
limited to 32 KiB per operation; concrete proposal review to 128 KiB. Split large
operations rather than skipping the review.

Scoped live tests verified public character/journal creation and dependent posts
from reviewed private notes under a non-admin account. Publication edits have
mocked coverage, not a separate live trial. This is not separate player/GM
login authentication. Anyone with access to the local token and stores must be
trusted. For uncertain writes, use the [creation](supervised-creation.md#if-execution-fails)
or [edit recovery](supervised-editing.md#unknown-results-and-recovery) procedure;
do not resend just because a record is not visible.
