# Supervised creation: first offline slice

The production MCP client and all eight tools remain GET-only. This slice adds
a reusable proposal and approval boundary, exercised by the isolated rehearsal.
It supplies no live writer, mutation MCP tools, authentication service or UI.

## Proposal and approval contract

1. Validate an immutable `CharacterDraft`. The draft supports a description,
   outgoing one-way relations, linked inventory and posts. Unknown fields,
   boolean IDs and invalid values fail validation. References use universal
   entity IDs; the executor resolves item module IDs before execution.
2. `ApprovalGate.prepare` checks the whole proposal against explicitly allowed
   campaign IDs and operation classes: `character.create`, `relation.create`,
   `inventory.add`, `post.create`. The default scope permits nothing. It stores a
   validated copy and returns a proposal ID, ordered preview, content checksum
   and remaining lifetime. Returned preview dictionaries cannot edit that copy.
3. A **trusted host**, after authenticating the reviewer and displaying the exact
   stored proposal, calls `approve` with its ID, reviewed checksum and reviewer
   identity. The checksum is public review metadata; it cannot authorize a run.
   The host must never accept an LLM-supplied claim of human approval. The current
   Python `approved_by` argument records attribution, not authentication.
4. `approve` issues an opaque random `ApprovalGrant`. Only that grant can be
   claimed. Tokens must stay outside model-visible previews, logs and tool
   results. The proposal expires after five minutes by default; approval does
   not extend its lifetime. Pending records are bounded and expired/revoked
   records are removed when preparing another proposal.
5. `claim` consumes the grant atomically and returns the server-stored draft with
   approval audit metadata. Concurrent claims cannot both succeed. Forged,
   foreign, expired, revoked or previously consumed grants are rejected.
6. Scope, expiry and revocation are checked again before every simulated POST.
   A policy change also applies to existing proposals/grants. Approval and policy
   methods are trusted backend calls, not model-callable MCP tools. A restart
   discards all approval records and does not restore execution permission.

The module contains no HTTP executor. Only the rehearsal uses these grants now.
Its CLI `--approve CONTENT_DIGEST` is the trusted adapter for reviewing a fixed
synthetic example and issuing a grant within that same process.

## Execution and failure behavior in the rehearsal

Both HTTP clients always use `httpx.MockTransport`. There is no token loading or
live transport option. Runs are serialized within a rehearsal instance. It
creates the approved character, relation, inventory entry and two posts, then
compares aggregate read-back to the draft. The progress journal skips confirmed
steps on a same-instance retry. Literal placeholder text in descriptions/posts
is preserved; only typed owner/item fields and fixed endpoint templates are bound.

| Outcome | Behavior |
| --- | --- |
| Confirmed success | Record the step and continue; compare final read-back |
| Known HTTP rejection | Report completed steps; a new proposal/grant is required to resume |
| Scope/approval becomes invalid | Stop before the next POST and report confirmed progress |
| Partial-run retry | Re-read completed data first; changed or unavailable data blocks continuation |
| Timeout, lost/malformed success response, cancellation during send, unexpected redirect or ambiguous HTTP failure | Mark the step uncertain and block blind retry, even with new approval |
| Verification failure | Report the mismatch; do not claim success or delete records |

Audit output includes proposal ID, content checksum and reviewer. It contains no
grant token. Discarding the rehearsal discards its synthetic campaign and default
in-memory journal. An explicitly supplied durable journal remains on disk;
there are no cleanup or rollback HTTP requests.

## Durable progress and explicit reconciliation

The [synthetic acceptance workflow](synthetic-creation-validation.md) now has
focused SDK dispatch and actual stdio coverage against freshly created mock state.
The user separately accepted the manual live example and deleted its records.
The campaign-specific live script is not retained as a product feature. Test
servers remain under `tests/`, outside the installed package; they expose only
the existing eight read tools. Retire obsolete demonstration scaffolding for the
final release while preserving automated regression coverage.

`loremaster.progress.CreationJournal` uses standard-library SQLite. Nothing opens
a database by default. Python rehearsal callers must explicitly provide both a
private journal and an execution ID:

```python
store = CreationJournal(private_path)  # Existing private parent, outside Git.
rehearsal = OfflineCreation(progress_store=store, execution_id="session-test-1")
```

The caller closes both the rehearsal and `store`. The CLI remains a disposable
in-memory example. Restart tests preserve the simulated upstream campaign while
reopening the executor; the journal does not persist a fake campaign or revive
an expired/consumed approval.

- The execution ID identifies the logical request independently of its content
  checksum. Bindings include campaign, digest and resolved universal/module item
  references. A changed draft/reference cannot reuse the execution ID.
- Each step records the exact resolved POST fingerprint, request body, reviewer
  and approval reference. Intent commits **before** sending. Atomic intent checks
  reject competing sends of the same step.
- Confirmed results retain record/universal/owner IDs. These are response
  identities, not a claim that final lore verification already passed. Every
  resume re-reads completed fields **and IDs**, and final read-back is still required.
- An interrupted intent or uncertain response blocks sends after restart. Known
  rejections may resume with a fresh grant; approvals themselves remain process-local.
- `await rehearsal.reconcile(candidate_id)` inspects an explicit candidate using
  GETs and returns evidence without changing progress. Character candidates use
  universal IDs; attachments use their own record IDs. Missing, mismatched,
  inaccessible or incomplete evidence never clears uncertainty.
- After the trusted host has stopped other workers and verified the candidate's
  origin, it may call `reconcile(candidate_id, reviewed_by=...)` to record the
  matching evidence. Matching content alone does not prove origin. This operation
  issues no grant and sends no POST; resuming still requires fresh approval.

New journal files use mode 0600; insecure existing files and symlinks are rejected.
Bodies can contain private lore, so keep the file and its directory outside the
public repository. Bearer/approval tokens and raw upstream error bodies are not
stored. The store retains the current attempt per step, not a complete append-only
audit history. No migration or production journal retention policy is provided yet.

This is tested offline recovery, not production authorization, automatic
reconciliation or distributed transaction support. A host must authenticate
reviewers, stop workers during reconciliation and protect local storage. Approval
does not freeze referenced records; read-before-write checks cannot eliminate
upstream races. Unknown outcomes never become safe retries merely because a
search found nothing.

Before enabling any real execution, verify the actual write-response and
inventory-visibility contracts, obtain separate write consent, select an explicit
disposable campaign, connect an authenticated approval surface, and test the same
description/relation/inventory/two-post scenario. Cleanup must be explicitly
scoped. Image uploads, deletion, canon overwrites and automatic modes are later
capabilities requiring their own authorization and verification.
