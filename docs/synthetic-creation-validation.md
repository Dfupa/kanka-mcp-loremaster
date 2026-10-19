# Synthetic creation acceptance workflow

**Scenario ID: `synthetic-character-v1`. Status: automated server validation passed.**
Scope: offline only, using
the existing `OfflineCreation` fixture and mandatory HTTPX `MockTransport`.
This original checkpoint preceded durable progress and supervised execution;
those now have separate coverage described below. No Kanka account, real campaign
or API token is involved in these tests.

`tests/test_creation_server.py` now dispatches the frozen scenario through the
SDK and an actual stdio MCP session. It independently compares expected/observed
fields, verifies IDs, seed isolation, mutation counts and shutdown. The user's
separate manual live check was accepted; the user deleted those test entities.
The campaign-specific live helper is not part of the project or release.

The optional writer now reuses this frozen scenario through
`tests/test_supervised_execution.py` and `tests/test_supervised_stdio.py`, with
separate local approval and mocked HTTP. The original read-server fixture below
retains its eight-tool contract; it is not the optional typed-creation server.
See [the current execution contract](supervised-creation.md) for those additions.

## Frozen scenario

Use the current `example_draft()` and a fresh rehearsal instance for each case.
Do not expand the scenario with images, calendars or additional entity types.

| Record | Starting state |
| --- | --- |
| Campaign | Synthetic campaign `7`, available only inside the mock transport |
| Mentor | Existing `Synthetic mentor`; universal entity ID `101`, character module ID `11` |
| Item | Existing `Synthetic compass`; universal entity ID `201`, item module ID `21` |
| New character | Does not exist; inventory, relations and posts are initially absent |

These are test fixture IDs, not an operational name-to-ID dictionary. Allocate
the new character's IDs from the create response and verify their meaning;
do not assume its universal ID equals its character module ID.

| Approved field | Exact expected value |
| --- | --- |
| Character name | `LoreMaster test — Élan` |
| Description | `<p>Synthetic character created for an offline rehearsal.</p>` |
| Character privacy | `is_private: true` |
| Relation | New character → mentor `101`; label `Mentor`, attitude `25`, visibility `3` (admin), `two_way: false` |
| Inventory | Reference universal item `201`, resolve module item `21`; amount `"2"`, equipped `true`, position `Belt` |
| Post one | `Test post one`; `<p>First synthetic note.</p>`; visibility `3` (admin) |
| Post two | `Test post two`; `<p>Second synthetic note.</p>`; visibility `3` (admin) |

The mentor/item are seeded dependencies. Creating them is not part of the five
operations. The mock creates an outgoing edge without a reciprocal edge; this
does not promise live one-way behavior from Kanka.
Inventory visibility behavior is not established by this test: it is not an
explicit field in the current draft. Verify only the approved fields above.

## Sequence and observable outcomes

1. **Establish a clean baseline.** Snapshot the seeded records/attachments and
   initialize request capture. Prohibit real HTTP transport use. The fixture's
   high synthetic quota keeps tests fast; it is not an account-tier benchmark.
2. **Preview without execution.** Generate the ordered proposal. Review every
   body, target, privacy field and the `content_digest`. Expect exactly five
   POST descriptions and no actual HTTP requests from preview generation.
3. **Approve the exact proposal.** Prepare its immutable backend copy and use the
   trusted offline reviewer to issue a grant. Record proposal ID, checksum and
   reviewer. The checksum alone must not authorize a run. Never display the
   grant token in the review report.
4. **Resolve references.** Verify mentor/item ownership and identity within the
   synthetic campaign. Resolve item universal ID `201` to module ID `21` and
   reject duplicate character names before the first simulated POST.
5. **Execute once.** Send the five simulated operations below in order. Preserve
   response IDs and verify parent/owner identity. There must be no PATCH, PUT,
   DELETE, extra POST, reciprocal relation or automatic cleanup request.
6. **Read through MCP.** Bind `create_server(rehearsal.reader)` to the same mock
   state. Dispatch `get_entity`, `get_entity_inventory`, `get_entity_relations`,
   `get_entity_posts` and `get_entity_full` through the installed SDK's tool
   dispatcher. Compare their structured results with the frozen expected table,
   independently of the rehearsal's completion labels. This checks MCP read
   dispatch; creation itself remains a Python/mock workflow, not an MCP tool.
7. **Check isolation and close.** Compare seeded records/attachments with their
   baseline, check the request trace and close both mock HTTP clients. Discard
   the synthetic state. The validation must not make cleanup writes.
8. **Review the report.** Present expected/observed field checks, operation counts
   and negative-case outcomes. Only advance to the next development milestone
   after this checkpoint has been run and the results reviewed.

| Order | Endpoint relative to `campaigns/7/` | Required identity |
| --- | --- | --- |
| 1 | `characters` | Return distinct, positive universal and character IDs |
| 2 | `entities/{new_entity_id}/relations` | Owner is the new universal ID; target is `101` |
| 3 | `entities/{new_entity_id}/inventory` | Parent is the new universal ID; `item_id` is `21` |
| 4 | `entities/{new_entity_id}/posts` | First post belongs to the new universal ID |
| 5 | `entities/{new_entity_id}/posts` | Second post has its own record ID and the same parent |

## Negative cases for this checkpoint

Run each case with fresh synthetic state. Preserve the existing broader unit
suite; these cases define the focused acceptance report.

| Case | Expected outcome |
| --- | --- |
| No grant, checksum used as grant, invalid/expired/revoked grant | Rejected before execution; zero POSTs |
| Changed draft submitted with an old reviewed checksum, wrong campaign or forbidden operation class | Rejected; zero POSTs |
| Caller edits a returned preview dictionary | Backend copy remains unchanged; the grant can only execute the stored content |
| Missing reference, wrong item type or duplicate character name | Preflight failure; mock GETs may occur, zero POSTs |
| Second post rejected with HTTP 403 | Five POST attempts, four completed steps, one stored post; report `partial`, failed step `post:1`; no rollback or automatic retry |
| Response lost after a post was stored | Report `uncertain`; do not claim absence or retry; seeded data remains unchanged |
| Read-back field differs from the approved value | Fail verification and identify the mismatch; do not report success |
| Previously consumed grant submitted again | Rejected; no additional HTTP requests or created records |

The partial/uncertain cases end with accurate reporting and stopped execution.
Recovery is a subsequent, separately tested layer in `tests/test_progress.py`;
it does not change these acceptance expectations or grant live write permission.

## Validation commands and report

The existing CLI supports preview and mock execution:

```bash
python scripts/offline_creation.py
python scripts/offline_creation.py --approve CONTENT_DIGEST_FROM_REVIEWED_PREVIEW
pytest -q tests/test_creation.py tests/test_offline_creation.py
pytest -q -rP tests/test_creation_server.py
pytest -q tests/test_progress.py
```

Use the returned checksum rather than hardcoding it. The CLI currently prints a
completion/audit summary and omits the aggregate bundle. During the validation
step, the focused server tests perform the MCP reads, frozen field comparisons
and seed-isolation checks above. `-rP` prints the expected/observed JSON report
for passing tests. Do not infer these checks passed from the CLI's
`status: verified` line alone. The tests need no live writer or credentials.

The human-readable report must include:

- Tested commit and fixture version, with explicit `offline` environment.
- Each approved field's expected and observed value, including both post bodies.
- Returned universal/module IDs, parent/target/item identity checks and sources.
- Actual POST count/order, absence of other mutation methods, and seed comparison.
- MCP read errors/completeness, negative-case outcomes and client cleanup result.
- Overall `PASS` or `FAIL`, with failures/unknowns listed; no grant tokens.

**Acceptance:** all frozen field, identity, count, isolation, approval and cleanup
checks pass; both posts are present exactly once; all focused negative cases
behave as specified; no real HTTP transport is used. These results demonstrate
the synthetic workflow only, without establishing Kanka's live API behavior.

| Evidence | Current status |
| --- | --- |
| Scenario and acceptance design | Implemented in focused tests |
| Focused workflow validation and expected/observed report | SDK and actual stdio checks passed; reproduce with `-rP` above |
| Manual example review | User accepted the live test and reported deleting its records |
| Durable tracking/reconciliation implementation | Implemented for optional offline rehearsal use; tested separately |

The test fixture retains its deterministic outgoing relation expectation. The
user asked not to pursue the live `two_way` discrepancy; it is not a blocker for
this slice, and no live one-way guarantee is claimed.


## Extended MCP-only workflow: organizations and items

**Scenario ID: `synthetic-entity-workflow-v2`. Offline only; no live campaign IDs.**
This extends acceptance coverage without changing the frozen v1 fixture above.
`tests/test_supervised_stdio.py::test_entire_synthetic_workflow_uses_mcp_and_local_approvals`
launches the normal server entry point with forced mock HTTP and a separate local
approval process. Existing fixture seeds stay unchanged; the workflow creates its
own dependencies. Species and location remain pre-existing synthetic references.

| Order | Proposal | Mocked POSTs | Required read-back |
| --- | --- | --- | --- |
| 1 | Synthetic mentor character | 1 | Distinct universal/module IDs; public core |
| 2 | Synthetic compass item | 1 | Type/price/size/weight text and public core; no inventory side effect |
| 3 | Synthetic Éldan character | 5 | Description, age/sex, species/location, relation to the returned mentor ID, inventory linked to the returned item ID, two posts |
| 4 | Synthetic organization | 3 | Public organization and two public active `Member` entries, referencing Éldan and the mentor through resolved character module IDs |

Each proposal must be prepared through its typed MCP tool, denied before approval,
reviewed/approved through the separate terminal command, then executed through
`execute_creation`. Membership is recorded through Kanka's dedicated endpoint,
not generic relations or descriptions. Use returned IDs throughout; fixture IDs
are not operational lookup tables. There are ten POSTs total, no direct setup
writes, PATCH/PUT/DELETE or cleanup calls. Closing the server discards mock state.

Focused organization/item tests add invalid member types, duplicate characters,
privacy coercion, per-operation scope, changed references, missing pagination,
wrong owners/campaigns, replaced IDs, partial rejection/restart, uncertain core and
membership responses, candidate reconciliation and cancellation. The same durable
approval/journal/verification implementation handles all three core types.

Run the extended checks without credentials:

```bash
pytest -q tests/test_organisation_creation.py tests/test_item_creation.py tests/test_supervised_stdio.py
```

Passing offline tests alone does not establish live response schemas. After
reviewing draft PR #4, the user separately authorized a public synthetic live
run and recreation of its deleted character dependencies. The normal MCP server
and separate local approval command completed the same four-proposal sequence:
ten confirmed POST steps, verified core/attachment fields, complete membership
pagination and returned-ID binding. Live membership roles were apprentice and
mentor rather than the offline fixture's generic member role. Independent reads,
consumed-approval replay denial, durable journal reopening and shutdown passed.
Records remain for manual inspection; there was no cleanup. Campaign IDs, payloads,
credentials and the temporary helper stay outside this public repository. This
acceptance applies to one account/campaign; future live scenarios need fresh
identity resolution and explicit campaign/operation/content authorization.
