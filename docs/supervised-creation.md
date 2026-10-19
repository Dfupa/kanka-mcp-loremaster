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
grant token. Discarding the rehearsal discards its synthetic campaign and journal;
there are no cleanup or rollback HTTP requests.

## Recovery work before a real executor

The next checkpoint is the [offline synthetic acceptance workflow](synthetic-creation-validation.md).
Review and validate that scenario before implementing the recovery work below.
Its acceptance/reporting status is tracked in that runbook; existing regression
test success alone does not complete the checkpoint.

The gate and journal are process-local. They do not provide durable idempotency,
multi-process coordination, atomic Kanka transactions or production authorization.
Approval does not freeze referenced records. Current same-instance read-back
checks detect edits to completed data but cannot eliminate races between reads
and writes. Human identity/roles and private storage of proposals/audit data are
the responsibility of a future authenticated host.

A future durable journal must bind a unique proposal/operation ID to the approved
content, campaign, resolved references and request fingerprint; record intent
before sending; and retain confirmed result IDs and verification state. A content
hash alone must not conflate a retry with a separately approved new request.
Do not persist tokens or campaign payloads in this public repository.

After a crash or uncertain response, inspect authoritative scoped records and
match the operation's identity/content before marking it complete. Missing or
ambiguous evidence requires human reconciliation. A new approval must never
erase uncertainty or trigger an automatic retry of an uncertain POST. Storage and
reconciliation adapters are intentionally not implemented in this slice.

Before enabling any real execution, verify the actual write-response and
inventory-visibility contracts, obtain separate write consent, select an explicit
disposable campaign, connect an authenticated approval surface, and test the same
description/relation/inventory/two-post scenario. Cleanup must be explicitly
scoped. Image uploads, deletion, canon overwrites and automatic modes are later
capabilities requiring their own authorization and verification.
