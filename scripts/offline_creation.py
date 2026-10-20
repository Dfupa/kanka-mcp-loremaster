"""Network-isolated creation rehearsal; no live write adapter or MCP write tool.

Run without arguments to review a synthetic proposal, then --approve DIGEST to
execute it against an in-memory HTTPX MockTransport. No token/environment reads,
real requests, automatic rollback or deletion are possible here. Python callers
may explicitly supply a private CreationJournal to rehearse restart recovery.
The CLI is the trusted approval caller for this offline scenario. A content
checksum is not an execution grant; no model-callable approval tool exists.
"""

import argparse
import asyncio
import json
from typing import Any

import httpx

from loremaster.aggregate import get_entity_full
from loremaster.creation import (
    ApprovalGate,
    ApprovalGrant,
    CharacterDraft,
    CreationScope,
    InventoryDraft,
    PostDraft,
    RelationDraft,
    preview_creation,
    resolve_creation_request,
)
from loremaster.kanka import API_BASE_URL, KankaClient, KankaError
from loremaster.progress import CreationJournal
from loremaster.verification import verify_created, verify_result_ids


class OfflineCreation:
    """A disposable synthetic campaign whose HTTP transport cannot reach Kanka.

    Successful steps are journaled in memory or an explicitly supplied store. Rejections
    may be resumed; timeouts/5xx require reconciliation and are never blindly
    retried. This is not durable idempotency for a production write service.
    """

    campaign_id = 7

    def __init__(
        self,
        gate: ApprovalGate | None = None,
        *,
        progress_store: CreationJournal | None = None,
        execution_id: str | None = None,
    ) -> None:
        if (progress_store is None) != (execution_id is None):
            raise ValueError("Durable rehearsal requires both a store and execution ID")
        if progress_store is not None:
            progress_store.progress(execution_id)
        self.progress_store = progress_store
        self.execution_id = execution_id
        self.gate = (
            gate
            if gate is not None
            else ApprovalGate(
                CreationScope(
                    campaign_ids=frozenset({self.campaign_id}),
                    operations=frozenset(
                        {
                            "character.create",
                            "relation.create",
                            "inventory.add",
                            "post.create",
                        }
                    ),
                )
            )
        )
        self.records = {
            101: {
                "id": 11,
                "entity_id": 101,
                "name": "Synthetic mentor",
                "entry": "Mentor",
            },
            201: {
                "id": 21,
                "entity_id": 201,
                "name": "Synthetic compass",
                "entry": "Item",
            },
        }
        self.attachments: dict[int, dict[str, list[dict[str, Any]]]] = {}
        self.journal: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []
        self._execution_lock = asyncio.Lock()
        self.failure: tuple[str, int] | None = None
        self.lose_response: str | None = None
        self._active_key = ""
        self._writer = httpx.AsyncClient(
            base_url=API_BASE_URL, transport=httpx.MockTransport(self._handle)
        )
        self.reader = KankaClient(
            "offline-dummy-token",
            allowed_campaign_ids=frozenset({self.campaign_id}),
            admin_campaign_ids=frozenset({self.campaign_id}),
            transport=httpx.MockTransport(self._handle),
        )

    async def aclose(self) -> None:
        """Release both mock clients; synthetic state dies with the instance."""
        await self._writer.aclose()
        await self.reader.aclose()

    def _entity(self, entity_id: int) -> dict[str, Any]:
        record = self.records[entity_id]
        return {
            "id": entity_id,
            "campaign_id": self.campaign_id,
            "child_id": record["id"],
            "name": record["name"],
            "type": record.get(
                "entity_type", "item" if entity_id == 201 else "character"
            ),
        }

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """Serve synthetic scoped endpoints; no network fallback exists."""
        path = request.url.path.removeprefix("/1.0/")
        self.requests.append((request.method, path))

        def response(data):
            return httpx.Response(
                200,
                json={
                    "data": data,
                    "meta": {"last_page": 1, "current_page": 1, "per_page": 45},
                },
            )

        if request.method == "GET" and path == "profile":
            return response({"rate_limit": 1_000_000, "default_pagination": 45})
        if not path.startswith("campaigns/7/"):
            return httpx.Response(403)
        parts = path.split("/")
        if request.method == "GET":
            if path == "campaigns/7/entities":
                name = request.url.params.get("name", "").casefold()
                return response(
                    [
                        self._entity(key)
                        for key, record in self.records.items()
                        if name in record["name"].casefold()
                    ]
                )
            if len(parts) == 4 and parts[2] in (
                "characters",
                "items",
                "races",
                "locations",
                "organisations",
            ):
                for key, record in self.records.items():
                    if (
                        record["id"] == int(parts[3])
                        and parts[2] == self._entity(key)["type"] + "s"
                    ):
                        return response(record)
            if (
                len(parts) >= 4
                and parts[2] == "entities"
                and int(parts[3]) in self.records
            ):
                entity_id = int(parts[3])
                if len(parts) == 4:
                    return response(self._entity(entity_id))
                if parts[4] == "image":
                    return httpx.Response(200, json={"image": None, "header": None})
                return response(self.attachments.get(entity_id, {}).get(parts[4], []))
            return httpx.Response(404)
        if request.method != "POST":
            return httpx.Response(405)
        if self.failure and self.failure[0] == self._active_key:
            return httpx.Response(self.failure[1], text="SYNTHETIC PRIVATE FAILURE")
        body = json.loads(request.content)
        if path == "campaigns/7/characters":
            entity_id = max(self.records) + 1
            data = {**body, "id": entity_id + 1000, "entity_id": entity_id}
            self.records[entity_id] = data
        elif (
            len(parts) == 5
            and parts[2] == "entities"
            and int(parts[3]) in self.records
            and parts[4] in ("posts", "inventory", "relations")
        ):
            entity_id = int(parts[3])
            rows = self.attachments.setdefault(entity_id, {}).setdefault(parts[4], [])
            data = {**body, "id": len(rows) + 1}
            rows.append(data)
        else:
            return httpx.Response(404)
        if self.lose_response == self._active_key:
            raise httpx.ReadTimeout("Synthetic lost response", request=request)
        return response(data)

    def prepare(self, draft: CharacterDraft) -> dict[str, Any]:
        """Prepare a scoped synthetic proposal without approving or executing it."""
        draft = CharacterDraft.model_validate(draft.model_dump())
        if draft.campaign_id != self.campaign_id:
            raise PermissionError("Only the synthetic campaign is permitted")
        return self.gate.prepare(draft)

    async def run(self, grant: ApprovalGrant) -> dict[str, Any]:
        """Claim a grant and execute its stored draft through MockTransport only."""
        async with self._execution_lock:
            return await self._run(grant)

    async def _run(self, grant: ApprovalGrant) -> dict[str, Any]:
        """Serialize rehearsal state so concurrent approvals cannot duplicate steps."""
        approved = self.gate.claim(grant)
        draft = approved.draft
        if draft.campaign_id != self.campaign_id:
            raise PermissionError("Only the synthetic campaign is permitted")
        proposal = preview_creation(draft)
        audit = {
            "proposal_id": approved.proposal_id,
            "content_digest": approved.content_digest,
            "approved_by": approved.approved_by,
        }
        if self.progress_store is not None:
            journal = self.progress_store.progress(self.execution_id)
            self.journal[self.execution_id] = journal
        else:
            journal = self.journal.setdefault(approved.content_digest, {})
        if "uncertain" in journal:
            raise RuntimeError(
                "An uncertain operation requires reconciliation before retry"
            )

        module_ids = {}
        for relation in draft.relations:
            await self.reader.get_entity_metadata(draft.campaign_id, relation.target_id)
        for item in draft.inventory:
            linked = await self.reader.get_entity(
                draft.campaign_id, item.item_entity_id
            )
            if linked["entity"]["entity_type"] != "item":
                raise ValueError("Inventory references must identify item entities")
            module_ids[item.item_entity_id] = linked["entity"]["child_id"]
        for expected, references in (
            ("race", draft.race_entity_ids),
            ("location", draft.location_entity_ids),
        ):
            for uid in references:
                linked = await self.reader.get_entity(draft.campaign_id, uid)
                if linked["entity"]["entity_type"] != expected:
                    raise ValueError(f"Character reference must identify a {expected}")
                module_ids[uid] = linked["entity"]["child_id"]
        if self.progress_store is not None:
            self.progress_store.bind(self.execution_id, approved, module_ids)
        if "character" not in journal:
            matches = await self.reader.find_entities_by_name(
                draft.campaign_id, draft.name
            )
            if matches["exact"]:
                raise ValueError(
                    "Character name already exists; resolve the conflict first"
                )
        else:
            current = await get_entity_full(
                self.reader,
                draft.campaign_id,
                journal["character"]["entity_id"],
                sections=["posts", "inventory", "relations"],
            )
            if (
                current["entity"]["entity_type"] != "character"
                or current["entity"]["child_id"] != journal["character"]["id"]
            ):
                raise KankaError(
                    "Previously created character changed; reconcile first"
                )
            completed = draft.model_copy(
                update={
                    section: tuple(
                        value
                        for index, value in enumerate(getattr(draft, section))
                        if f"{prefix}:{index}" in journal
                    )
                    for section, prefix in (
                        ("relations", "relation"),
                        ("inventory", "inventory"),
                        ("posts", "post"),
                    )
                }
            )
            verify_created(completed, current, module_ids)
            verify_result_ids(journal, current)

        for operation in proposal["operations"]:
            key = operation["key"]
            if key in journal:
                continue
            entity_id = journal.get("character", {}).get("entity_id")
            path, body = resolve_creation_request(
                draft, key, entity_id=entity_id, module_ids=module_ids
            )
            self._active_key = key
            intent_started = False
            try:
                self.gate.check_execution(approved)
                if self.progress_store is not None:
                    self.progress_store.begin(
                        self.execution_id, approved, key, path, body
                    )
                    intent_started = True
                response = await self._writer.post(path, json=body)
                if response.status_code >= 300 and response.status_code not in (
                    400,
                    401,
                    403,
                    404,
                    409,
                    422,
                    429,
                ):
                    journal["uncertain"] = key
                response.raise_for_status()
                created = response.json()["data"]
                if (
                    not isinstance(created, dict)
                    or type(created.get("id")) is not int
                    or created["id"] <= 0
                ):
                    raise ValueError("Invalid synthetic creation response")
                owner_field = "owner_id" if key.startswith("relation:") else "entity_id"
                if (
                    type(created.get(owner_field)) is not int
                    or created[owner_field] <= 0
                    or (key != "character" and created[owner_field] != entity_id)
                ):
                    raise ValueError("Mismatched synthetic creation response")
            except asyncio.CancelledError:
                journal["uncertain"] = key
                if intent_started:
                    self.progress_store.finish(self.execution_id, key, "uncertain")
                raise
            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                PermissionError,
                ValueError,
                KeyError,
                TypeError,
            ) as error:
                if isinstance(
                    error, (httpx.RequestError, ValueError, KeyError, TypeError)
                ):
                    journal["uncertain"] = key
                if intent_started:
                    self.progress_store.finish(
                        self.execution_id,
                        key,
                        "uncertain" if "uncertain" in journal else "rejected",
                    )
                return {
                    "status": "uncertain"
                    if "uncertain" in journal
                    else "partial"
                    if journal
                    else "failed",
                    "completed": [step for step in journal if step != "uncertain"],
                    "failed_step": key,
                    "entity_id": entity_id,
                    "approval": audit,
                    "error_code": "approval_denied"
                    if isinstance(error, PermissionError)
                    else "uncertain_result"
                    if "uncertain" in journal
                    else "upstream_rejected",
                    "error": str(error)
                    if isinstance(error, PermissionError)
                    else "Synthetic operation failed; no automatic rollback",
                }
            if self.progress_store is not None:
                self.progress_store.finish(self.execution_id, key, "confirmed", created)
            journal[key] = created
        entity_id = journal["character"]["entity_id"]
        bundle = await get_entity_full(self.reader, draft.campaign_id, entity_id)
        verify_created(draft, bundle, module_ids)
        verify_result_ids(journal, bundle)
        return {
            "status": "verified",
            "completed": list(journal),
            "entity_id": entity_id,
            "bundle": bundle,
            "approval": audit,
        }

    async def reconcile(
        self, candidate_id: int, *, reviewed_by: str | None = None
    ) -> dict[str, Any]:
        """Inspect an explicitly selected record; optionally record trusted review.

        Use a universal ID for a character candidate, attachment record ID for
        other steps. Missing/mismatched evidence leaves uncertainty intact. The
        trusted host must stop other workers and establish the candidate's origin
        before passing reviewed_by; matching content alone cannot prove origin.
        No grant is issued and no HTTP mutation occurs. Resume needs fresh approval.
        """
        if self.progress_store is None:
            raise ValueError("Reconciliation requires a durable execution journal")
        self.reader._check_id(candidate_id, "candidate_id")
        if reviewed_by is not None and (
            not isinstance(reviewed_by, str) or not reviewed_by.strip()
        ):
            raise ValueError("Reconciliation requires an identified reviewer")
        async with self._execution_lock:
            pending = self.progress_store.pending(self.execution_id)
            if pending["campaign_id"] != self.campaign_id:
                raise PermissionError("Pending operation belongs to another campaign")
            key, body = pending["key"], pending["body"]
            if key == "character":
                if pending["path"] != f"campaigns/{self.campaign_id}/characters":
                    raise ValueError("Invalid pending character path")
                try:
                    core = await self.reader.get_entity(self.campaign_id, candidate_id)
                except KankaError as error:
                    if error.status_code not in (403, 404):
                        raise
                    return {"status": "unavailable", "key": key}
                if core["entity"]["entity_type"] != "character":
                    return {"status": "mismatch", "key": key}
                candidates = [core["details"]]
            else:
                section = {
                    "relation": "relations",
                    "inventory": "inventory",
                    "post": "posts",
                }.get(key.split(":")[0])
                if section is None:
                    raise ValueError("Unsupported pending operation")
                owner = body.get("owner_id" if section == "relations" else "entity_id")
                self.reader._check_id(owner, "owner_id")
                if (
                    pending["path"]
                    != f"campaigns/{self.campaign_id}/entities/{owner}/{section}"
                ):
                    raise ValueError("Invalid pending attachment path")
                bundle = await get_entity_full(
                    self.reader, self.campaign_id, owner, sections=[section]
                )
                if (
                    bundle["missing_data"]
                    or not bundle["sections"][section]["complete"]
                ):
                    return {"status": "incomplete", "key": key}
                candidates = [
                    row
                    for row in bundle["sections"][section]["data"]
                    if type(row.get("id")) is int and row["id"] == candidate_id
                ]
            if len(candidates) != 1:
                return {"status": "missing_or_ambiguous", "key": key}
            candidate = candidates[0]
            if any(
                type(candidate.get(field)) is not type(value)
                or candidate[field] != value
                for field, value in body.items()
            ):
                return {"status": "mismatch", "key": key}
            result = {
                "status": "matching_candidate",
                "key": key,
                "candidate_id": candidate_id,
                "fingerprint": pending["fingerprint"],
                "source": API_BASE_URL + pending["path"],
            }
            if reviewed_by is not None:
                self.progress_store.confirm_reconciled(
                    self.execution_id,
                    pending["fingerprint"],
                    candidate,
                    reviewed_by=reviewed_by,
                )
                result["status"] = "confirmed"
            return result


def example_draft() -> CharacterDraft:
    """The requested synthetic description/relation/inventory/two-post scenario."""
    return CharacterDraft(
        campaign_id=7,
        name="LoreMaster test — Élan",
        entry="<p>Synthetic character created for an offline rehearsal.</p>",
        relations=(RelationDraft(target_id=101, relation="Mentor", attitude=25),),
        inventory=(
            InventoryDraft(
                item_entity_id=201, amount=2, is_equipped=True, position="Belt"
            ),
        ),
        posts=(
            PostDraft(name="Test post one", entry="<p>First synthetic note.</p>"),
            PostDraft(name="Test post two", entry="<p>Second synthetic note.</p>"),
        ),
    )


async def main() -> None:
    """Print a reviewable proposal or execute its explicit digest in memory only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--approve", help="Exact content_digest from the reviewed preview"
    )
    arguments = parser.parse_args()
    draft = example_draft()
    if arguments.approve is None:
        print(json.dumps(preview_creation(draft), indent=2, ensure_ascii=False))
        return
    rehearsal = OfflineCreation()
    try:
        proposal = rehearsal.prepare(draft)
        grant = rehearsal.gate.approve(
            proposal["proposal_id"],
            reviewed_digest=arguments.approve,
            approved_by="offline-cli-reviewer",
        )
        result = await rehearsal.run(grant)
        print(
            json.dumps(
                {key: value for key, value in result.items() if key != "bundle"},
                indent=2,
            )
        )
    finally:
        await rehearsal.aclose()


if __name__ == "__main__":
    asyncio.run(main())
