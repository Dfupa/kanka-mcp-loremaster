# Reviewed session-note publication for non-admin accounts

The default role is non-admin. Session notes can now propose public core records,
public posts and supported edits to public destinations through the existing
supervised MCP tools. Offline acceptance covers creation and supported edits.
A separately authorized live trial verified public character/journal creation
and two dependent public posts from an explicitly reviewed private synthetic source
under one non-admin account. Publication editing remains offline-tested only.

Public means Kanka's non-private setting, subject to the campaign's own access
rules. No setting here grants a permission the account lacks. Keep
`KANKA_ADMIN_CAMPAIGN_IDS` unset for a non-admin account. Existing write campaign
and operation allowlists and separate notes/approval databases still apply.

## Prepare an explicit public copy

Import and analyse the source normally. `ImportContext.audience` defaults to
`admin`; the original remains in the private local notes store. That label is
context, not authentication or permission to publish. A public operation needs
both `audience: "all"` and a `publication.rationale`. For example, using anchors
and claim/session keys from the actual imported source:

```json
{
  "key": "session-journal",
  "action": "create_entity",
  "entity_type": "journal",
  "session_key": "s12",
  "claim_keys": ["crossing"],
  "fields": {"name": "Session 12 — Crossing the bridge"},
  "audience": "all",
  "publication": {
    "rationale": "Publish the selected account after reviewing omitted GM context."
  },
  "body": [
    {"kind": "text", "text": "The group crossed the bridge and returned safely."},
    {
      "kind": "quote",
      "anchor": {"block_id": "b-4", "excerpt": "V: «no pienso bolver»"}
    }
  ]
}
```

The backend derives `is_private: false` for a public new core and `visibility_id: 1`
for a public new post. Callers cannot put visibility or arbitrary IDs in `fields`.
Existing edits preserve their destination's visibility; publication does not make
an existing private record public. Non-public references, private prerequisites,
uncertain identities, canon conflicts, partial reads and other blockers remain.

Private sources require an explicit public `body` for descriptions/posts. They
cannot use `session_text: true` or automatically publish the generated claim
summary. Metadata-only edits still show the source and exact proposed field diff.
Posts from private sources also need an explicit public title, rather than copying
a private session label. Source/claim audit annotations stay in local provenance
instead of being appended to public text.

If the source was explicitly classified as public, `session_text: true` is allowed
for a full corrected new journal. It still needs publication review. Real session
dates come from the existing anchored date parser; fantasy dates are not invented.
Posts retain grouping by owner/session/audience, with all contribution rationales
bound into the merged review. Different sessions stay separate.

Quotes preserve exact spelling and attribution. For a public quote, select the
complete protected speaker/dialogue span. A partial quote is rejected instead of
silently expanding into surrounding private context. Omit an unsuitable quote or
prepare a separately reviewed source revision; the system does not rewrite it.

## Review and release one operation

1. Inspect the full preview. Its `publication` section contains original session
   text, intended audience, rationale, exact public payload and source-to-public-HTML
   redaction diff. This is a local review artifact, not a Kanka write.
2. Prepare one selected operation with `prepare_session_note_handoff`, using the
   exact preview digest/version. Dependencies need completed, freshly verified
   IDs. Public prerequisites must also be public. The stored review includes the
   concrete bound draft and a publication review digest.
3. Run the existing local terminal command:

   ```bash
   kanka-loremaster-approve --database /absolute/private/supervised.sqlite3 review PROPOSAL_ID
   kanka-loremaster-approve --database /absolute/private/supervised.sqlite3 approve PROPOSAL_ID
   ```

   Add `--kind edit` for an edit. Review text, metadata, references and omitted
   context. Publication uses the distinct exact phrase displayed by the CLI:

   ```text
   publish PROPOSAL_ID REVIEW_DIGEST PUBLICATION_REVIEW_DIGEST
   ```

   The ordinary `approve ...` phrase cannot release a public note derivative.
   The stores enforce the additional publication digest before issuing approval;
   a model rationale, source label or chat reply is not an execution capability.
4. Execute that proposal through the existing `execute_creation` or `execute_edit`
   tool. The usual five-minute approval, scope, stale-baseline, uncertainty and
   verification guards remain. No publication approval/execute tool was added.
5. Use `inspect_session_note_handoff` with `verify_result: true` before dependent
   operations. Each later operation has its own terminal review and approval.

All publication/source metadata stays in the private local stores. Only the
reviewed Kanka request body is sent. Existing proposals, approval digests and
uncertain outcomes are not rewritten. Changing a public copy cannot reset an
existing logical handoff; review and explicitly revoke obsolete preparations.

## Canon privacy and practical bounds

Public references need explicitly public core records. Public canon citations are
limited to public core names/descriptions and public (`visibility_id: 1`) posts on
public owners. Other fields, inventory/relations evidence and unknown visibility
are unclassified for public citation and block release. This does not prevent
supported inventory/relation tools from operating under their own review policy.

The executor rechecks public source visibility and content through fixed GET
endpoints before writing. Changed/inaccessible sources stop the operation before
send; refresh the analysis and review rather than retrying an uncertain request.
These are preflight checks, not an upstream transaction or atomic version lock.

Original session review text is limited to 32 KiB per operation. Public source
counts use the service's existing per-call budget; concrete proposals retain the
128 KiB review limit. Split a large session/operation when a bound is reached.

The reviewer must judge whether free-form text and metadata are suitable for
release. This workflow provides exact-content approval and structured privacy
checks, not automatic semantic redaction or a separate GM/player authentication
system. Never share a GM token/store/server with players as a permission layer.

## Validation

The full editable-install, Ruff format/lint/format-check and Pytest sequence passed
with **1,022 tests**, including 26 publication acceptance cases. A synthetic
eight-operation workflow exercised new cores, dependent/public posts and supported
edits. The normal stdio entry point and separate terminal publication CLI also
passed with mandatory mocked HTTP and no admin declaration. Tests retain a
synthetic secret in local review while checking it is absent from all sent bodies.
The rebuilt wheel passed source matching, fixture exclusion, credential scanning,
tool counts, PDF worker and optimized runtime publication/quote guards.

After reviewing the exact public copies, the user authorized a four-operation live
trial. The production stdio MCP and separate terminal publication CLI created a
public character aged 27, a public dated journal and two public posts for separate
session dates. The posts were prepared only after the new journal's completed ID
was freshly GET-verified. Pre-send public-source checks used its fingerprint.
Each operation completed and independent GETs matched the reviewed names,
descriptions, exact speaker/dialogue text, public visibility, age/date and post
ownership. The two synthetic private lines and private labels were absent from
published text. Both posts appeared separately on the new journal.

No new uncertainty occurred. Prior authority/progress rows and all twelve older
uncertain intents were preserved, without retry, replacement, reset or
reconciliation. Fixtures remain for manual inspection. No existing-record edit,
deletion, commit, push, PR or new GitHub CI run was performed. This is scoped live
evidence for one account/campaign and an authored synthetic source; it does not
establish live publication editing, other permission combinations, OCR or a host
model's editorial accuracy on unseen notes.
