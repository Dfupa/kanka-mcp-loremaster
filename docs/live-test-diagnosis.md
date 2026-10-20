# Session-note live validation and implemented fixes

This consolidates the current evidence. Existing supported writes are documented
in [supervised creation](supervised-creation.md), [supervised editing](supervised-editing.md)
and [additional entities](additional-entities.md). Exact live payloads, IDs,
authority databases and execution logs remain private outside Git.

## Verified behavior

| Scope | Evidence | Limit |
| --- | --- | --- |
| Existing core/attachment edits | Earlier live trials verified character fields, species, tags, posts, inventory and relations, with independent GETs | Only the selected fields/account were exercised |
| Original session-note trial | Character correction and two dated quest posts verified; unrelated data preserved | Seven core creation outcomes remained uncertain |
| Private diagnostics | Five further uncertain outcomes captured with durable receipts | Private writes under the non-admin account were not verified |
| Public non-admin diagnostics | Five distinct core types completed and passed exact independent GET comparisons | Did not resolve old outcomes or publish private source |
| Reviewed private-source publication | Public character aged 27, dated journal and two dated posts completed through MCP and separate terminal review | One authored synthetic source/account/campaign; public-note editing remains offline-tested |

Publication posts were prepared only after the new journal's completed ID was
freshly GET-verified. Their manifests bound its public-source fingerprint, checked
before send. Independent reads matched IDs, names, descriptions, exact dialogue,
public visibility, age/date and ownership. Both dated posts appeared separately.
Private source lines/labels were absent. All four steps were confirmed; no new
uncertainty occurred. Fixtures remain for manual inspection; no cleanup is authorized.

## Twelve unresolved requests

Seven story creations and five private diagnostics remain uncertain. HTTP 500
does not prove rejection; HTTP 201 alone is not verified identity. Complete visible
generic/module/private-filter searches found no candidates. This does not prove
rollback or permit retry. Two later 201 diagnostics had complete, identical 29-byte
JSON bodies with array-shaped data and no usable identity. Older 201 responses
lack equivalent body evidence, so their exact cause cannot be reconstructed.
Kanka's internal 500 cause remains unknown. Later public-write success supports
a visibility/permission explanation, without proving the historical server cause.

All prior authority/progress rows and twelve intents remain preserved. No old
request was resent, replaced, reset or reconciled. Do not evade these guards with
new proposals/databases. Matching recovery fields require origin review; absence
alone cannot authorize a new POST.

## Implemented corrections

- **Diagnostics:** streaming POST/PATCH receipt capture records headers before
  body I/O, then bounded status/stage/shape/hash and positive candidate IDs.
  Categories distinguish transport/body failure, invalid JSON/envelope/identity,
  cancellation and HTTP errors. No upstream text, arbitrary keys, request body,
  credentials or exception text is logged. Attempt history does not change
  approval digests/outcomes. HTTPX hooks run before body completion; observing 201
  is insufficient. See [HTTPX hooks](https://www.python-httpx.org/advanced/event-hooks/).
- **Quota:** shared client pacing covers GET/POST/PATCH across operation contexts,
  using account quota and conservative effective headers. Local budgets stay
  separate. Long 429 cooldowns return retry guidance; writes are not automatically
  retried. Other processes can still consume quota. See [Kanka errors](https://app.kanka.io/api-docs/1.0/misc/errors).
- **Identity/recovery:** response-provided module IDs can be GET-verified against
  universal identity and approved fields. No coercion, guessing or first-name
  selection. `get_creation_status(inspect_recovery=true)` discovers candidates
  with bounded GETs, completeness and origin-review requirements; it never
  approves, reconciles or resends. Same-name unresolved-intent guards protect
  preparation, approval, claim and send.
- **Non-admin:** the default is non-admin. Only the host may declare an explicitly
  identified admin campaign through `KANKA_ADMIN_CAMPAIGN_IDS`; it grants no Kanka
  permission. Private core/member and visibility-2 writes stop before send.
  Visibility 3 includes the creator. Private payloads are not widened; recovery
  GETs remain available.
- **Publication:** explicit public copies bind original source, candidate,
  redaction diff, rationale and concrete IDs into terminal `publish` review.
  Executors recheck classified public-source privacy/content. See [the publication guide](non-admin-publication.md).
- **Final review:** dependency readiness now removes blockers resolved by verified
  prerequisite IDs, so a ready dependent post is visible. Attachment handoff keys
  distinguish exact record IDs; two post edits in one session no longer collide.
  Earlier handoff IDs/approvals remain intact. Focused mocked cases reproduced
  both defects before the fixes.

## Verification and delivery boundary

The suite uses invented data, mandatory mock HTTP, actual MCP stdio and separate
terminal approval. It covers response failures, 200/201, storage compatibility,
uncertainty preservation, cooldown, identity/source races, confidentiality, quotes
and dependencies. Final review adds two compatibility cases to the existing
bridge suite, with no new trial scripts. See [local verification](using-session-notes.md#local-verification)
and [required commands](../README.md#verification).

The next step is final review and PR preparation, not another generic live trial.
OCR, automatic revised-note mapping, live public-note editing and blind host-model
quality evaluation remain disclosed future work. Local success is not GitHub CI.
Historical test-count progressions and repeated trial logs are omitted here;
private detailed evidence remains intact.
