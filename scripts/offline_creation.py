"""Network-isolated creation rehearsal; no live write adapter or MCP write tool.

Run without arguments to review a synthetic proposal, then --approve DIGEST to
execute it against an in-memory HTTPX MockTransport. No token/environment reads,
real requests, persistence, automatic rollback or deletion are possible here.
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
)
from loremaster.kanka import API_BASE_URL, KankaClient, KankaError


class OfflineCreation:
    """A disposable synthetic campaign whose HTTP transport cannot reach Kanka.

    Successful steps are journaled only within this instance. Known rejections
    may be resumed; timeouts/5xx require reconciliation and are never blindly
    retried. This is not durable idempotency for a production write service.
    """

    campaign_id = 7

    def __init__(self, gate: ApprovalGate | None = None) -> None:
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
            "type": "item" if entity_id == 201 else "character",
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
            if len(parts) == 4 and parts[2] in ("characters", "items"):
                for key, record in self.records.items():
                    if record["id"] == int(parts[3]) and (parts[2] == "items") == (
                        key == 201
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
        journal = self.journal.setdefault(approved.content_digest, {})
        if "uncertain" in journal:
            raise RuntimeError(
                "An uncertain operation requires reconciliation before retry"
            )

        item_ids = {}
        for relation in draft.relations:
            await self.reader.get_entity_metadata(draft.campaign_id, relation.target_id)
        for item in draft.inventory:
            linked = await self.reader.get_entity(
                draft.campaign_id, item.item_entity_id
            )
            if linked["entity"]["entity_type"] != "item":
                raise ValueError("Inventory references must identify item entities")
            item_ids[item.item_entity_id] = linked["entity"]["child_id"]
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
            verify_created(completed, current, item_ids)

        for operation in proposal["operations"]:
            key = operation["key"]
            if key in journal:
                continue
            entity_id = journal.get("character", {}).get("entity_id")
            path = operation["path"].replace("{entity_id}", str(entity_id))
            body = dict(operation["body"])
            if key != "character":
                body["owner_id" if key.startswith("relation:") else "entity_id"] = (
                    entity_id
                )
            if key.startswith("inventory:"):
                body["item_id"] = item_ids[
                    draft.inventory[int(key.split(":")[1])].item_entity_id
                ]
            self._active_key = key
            try:
                self.gate.check_execution(approved)
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
            journal[key] = created
        entity_id = journal["character"]["entity_id"]
        bundle = await get_entity_full(self.reader, draft.campaign_id, entity_id)
        verify_created(draft, bundle, item_ids)
        return {
            "status": "verified",
            "completed": list(journal),
            "entity_id": entity_id,
            "bundle": bundle,
            "approval": audit,
        }


def verify_created(
    draft: CharacterDraft, bundle: dict[str, Any], item_ids: dict[int, int]
) -> None:
    """Compare retrieved core and attachments to the exact approved draft."""
    if bundle["missing_data"] or any(
        not part["complete"] for part in bundle["sections"].values()
    ):
        raise KankaError("Created entity could not be read back completely")
    details = bundle["details"]
    if any(
        details[key] != getattr(draft, key) for key in ("name", "entry", "is_private")
    ):
        raise KankaError("Created entity differs from the approved draft")
    expected = {
        "posts": [post.model_dump() for post in draft.posts],
        "relations": [
            {
                **relation.model_dump(),
                "owner_id": details["entity_id"],
                "two_way": False,
            }
            for relation in draft.relations
        ],
        "inventory": [
            {
                "item_id": item_ids[item.item_entity_id],
                "amount": str(item.amount),
                "is_equipped": item.is_equipped,
                "position": item.position,
            }
            for item in draft.inventory
        ],
    }
    for section, rows in expected.items():
        actual = bundle["sections"][section]["data"]
        if len(actual) != len(rows) or any(
            any(record.get(key) != value for key, value in row.items())
            for record, row in zip(actual, rows, strict=True)
        ):
            raise KankaError(f"Created {section} differ from the approved draft")


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
