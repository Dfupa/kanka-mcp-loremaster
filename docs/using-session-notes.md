# Session notes

Import a local TXT or PDF, ask your assistant to review it against the campaign,
then inspect the proposed changes. Importing and analysing notes does not write
to Kanka. Applying a proposal is a separate, approved operation.

The assistant interprets the notes; LoreMaster preserves the source, checks
references and validates the changes it proposes. No additional model API key
is needed by the Python server.

## Setup and import

Use the normal installation, adding `.[pdf]` if you need PDF support. Choose an
existing private directory outside Git and a notes database separate from your
approval database:

```bash
mkdir -p "$HOME/.local/share/kanka-mcp-loremaster"
chmod 700 "$HOME/.local/share/kanka-mcp-loremaster"
export KANKA_NOTES_DATABASE="$HOME/.local/share/kanka-mcp-loremaster/notes.sqlite3"
kanka-loremaster-notes session.txt \
  --database "$KANKA_NOTES_DATABASE" --campaign 123 --language es --locale dmy
```

Replace `123` with your campaign ID. Locale describes real-world date order:
`dmy`, `mdy` or `iso`. The import command needs no Kanka token and returns an
import `id` and `document_id`.

Pass `KANKA_NOTES_DATABASE` to the MCP server too, then restart it. This adds four
notes tools. The server still needs `KANKA_API_TOKEN` to read campaign evidence.
A private tunnel launcher may supply a fixed set of variables; exporting a setting
in your shell does not necessarily pass it to that launcher.

Use `--document-id DOCUMENT_ID` when importing a revision of the same document.
Identical source/range/encoding in that document can reuse an import. Revised
source creates a separate revision. Without that option, the import creates a
new document. The original source is never overwritten.

Small pasted notes can use `import_session_notes` instead. Its `context` accepts
`language`, `locale`, `audience` and `note_taker`. These describe the material;
they are not account permissions. Direct ChatGPT attachment download is deferred;
use local file import for now.

## Review a PDF

```bash
kanka-loremaster-notes sessions.pdf \
  --database "$KANKA_NOTES_DATABASE" --campaign 123 --language es --locale dmy \
  --pdf-start-page 1 --pdf-page-count 64
```

The original PDF is kept with page information, extraction warnings and hashes
used to check that the source has not changed. Inspect the `extraction` view of
`inspect_session_notes` before relying on the text. Every PDF needs visual review:
text order and extracted dialogue may differ from what appears on the page.

Image-only or unreadable pages are flagged. No OCR runs and no document is sent
to an external OCR service. Proposals based on uncertain pages remain blocked.
For another page range, repeat the command with the same `--document-id` and a
new `--pdf-start-page`. Ranges are separate imports, not an automatic merge.
Encrypted PDFs require a decrypted local copy.

## Analyse and review proposals

Ask the assistant to use the import ID from Terminal. The tools expose their
schemas and processing instructions; no checked-out Markdown is needed at runtime.

1. `inspect_session_notes` reads the source and current version. Follow
   `next_cursor` to see all pages.
2. `submit_session_note_analysis` supplies sessions, quoted source passages,
   narration corrections, names and attributed claims. Each correction identifies
   its exact source with a `block_id` and `excerpt`. Ambiguous excerpts need
   surrounding `before`/`after` context.
3. Follow `work_cursor` using `continuation` and the newest `expected_version`.
   LoreMaster reads the relevant Kanka records. Failed or incomplete lookups stay
   unresolved; they are not proof an entity is absent.
4. Inspect `evidence`, then call `prepare_session_note_proposals` with the current
   version and `evidence_digest`. Proposed actions must refer to their source and
   verified entities or explicit new-entity suggestions.
5. Inspect `preview` and `questions`. Review the corrected prose, actual proposed
   changes, privacy, conflicting accounts and unanswered questions.

Narration can be corrected, but protected dialogue and speaker attribution stay
exact. Missing characters, dates or acronym expansions must be resolved rather
than invented. A quoted claim is evidence of what someone said, not proof that
it is true. Corrections and plausible interpretations do not establish campaign
canon without review.

After restart, saved notes remain available but evidence must be refreshed.
On `note_revision_conflict`, inspect the latest version and deliberately resubmit;
do not overwrite someone else's analysis. The source language is preserved.

## Apply one selected proposal

To prepare notes for the existing writer, enable the
[write configuration](supervised-creation.md#enable-the-operations-you-need), keep
notes and approval databases separate, and set `KANKA_NOTES_HANDOFF=prepare` in
the server environment. This adds preparation/inspection tools; it grants no
approval or extra campaign permissions.

1. Select one unblocked operation from the preview.
2. Call `prepare_session_note_handoff` with `campaign_id`, `import_id`,
   `expected_version`, `preview_digest` and `operation_key`.
3. Review its returned proposal ID in Terminal and approve the exact change.
   Follow [creation](supervised-creation.md) or [editing](supervised-editing.md).
   Public copies require the separate [publication review](non-admin-publication.md).
4. Ask the assistant to call `execute_creation` or `execute_edit`.
5. Call `inspect_session_note_handoff` with `verify_result: true` to check the
   completed result before preparing dependent operations.

Each child or post needs its own approval after its owner's ID is verified.
Preparing an operation never approves or executes it. Changing the analysis does
not revoke an already prepared proposal; review and explicitly revoke obsolete
ones. Keep operation keys stable so interrupted preparation can recover the same
proposal rather than create a duplicate.

After failure, inspect status and follow the relevant recovery procedure. A new
proposal or database must not bypass an uncertain write. An empty search does not
prove a failed request had no effect.

## Privacy and limits

- Sources are stored privately. The default source audience is `admin`, which is
  a content label rather than user authentication. Do not share the local store
  as a player-facing permission system.
- New core proposals default to private, and posts to admins/creator visibility.
  The non-admin role restrictions still apply. Public copies require explicit
  selection and review; see [public copies](non-admin-publication.md).
- TXT/PDF input is limited to 8 MiB. Inline text and structured submissions are
  limited to 128 KiB each; large work must be split or continued.
- PDF extraction selects 64 pages by default, up to 256, from documents of at
  most 4,096 pages. A worker has a 30-second timeout and bounded output. Memory/
  CPU limits are applied where supported and reported in metadata; this is not
  an OS sandbox.
- Name/date ambiguity, unreadable pages, missing access and conflicting accounts
  remain visible. The preview is not a guarantee of editorial correctness.
- Automatic revision-to-record mapping, OCR, fantasy-calendar interpretation,
  inventory transfers and broader automatic changes are not implemented.
