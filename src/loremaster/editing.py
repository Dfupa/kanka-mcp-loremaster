"""Scoped single-PATCH edits with reviewed baselines and durable uncertainty."""

import asyncio
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any

import httpx

from loremaster.edit_models import (
    CORE_EDIT_ENDPOINTS,
    REFERENCE_FIELDS,
    EditDraft,
    build_edit_plan,
)
from loremaster.edit_store import EditStore
from loremaster.execution import KNOWN_REJECTIONS
from loremaster.kanka import API_BASE_URL, KankaClient, KankaError
from loremaster.publication import verify_publication_sources
from loremaster.verification import entry_matches
from loremaster.write_diagnostics import send_write

CORE_FIELDS = (
    "id",
    "entity_id",
    "name",
    "entry",
    "age",
    "sex",
    "races",
    "locations",
    "tags",
    "is_private",
    "updated_at",
    "title",
    "type",
    "families",
    "private_races",
    "private_locations",
    "private_families",
    "pronouns",
    "is_template",
    "status_id",
    "parent_id",
)
RECORD_FIELDS = {
    "post": (
        "id",
        "entity_id",
        "name",
        "entry",
        "visibility_id",
        "updated_at",
        "position",
        "settings",
        "layout_id",
        "tags",
        "calendar_id",
        "calendar_year",
        "calendar_month",
        "calendar_day",
    ),
    "inventory": (
        "id",
        "entity_id",
        "item_id",
        "name",
        "amount",
        "is_equipped",
        "position",
        "is_private",
        "visibility_id",
        "visibility",
        "updated_at",
    ),
    "relation": (
        "id",
        "owner_id",
        "target_id",
        "relation",
        "attitude",
        "is_pinned",
        "colour",
        "visibility_id",
        "updated_at",
    ),
    "tag": ("id", "tag_id", "updated_at"),
}
LORE_GUARD_FIELDS = (
    "members",
    "instigator_id",
    "author_id",
    "date",
    "location_id",
    "elements",
    "calendar_id",
    "calendar_year",
    "calendar_month",
    "calendar_day",
    "calendar_event_length",
    "is_pinned",
    "is_real",
    "height",
    "width",
    "grid",
    "min_zoom",
    "max_zoom",
    "initial_zoom",
    "center_marker_id",
    "center_x",
    "center_y",
    "layers",
    "groups",
    "image",
    "has_custom_image",
    "entity_image_uuid",
    "entity_header_uuid",
    "tooltip",
)


def positive(value: Any) -> bool:
    """IDs must be positive integers, never bools or coerced numeric strings."""
    return type(value) is int and value > 0


def comparable(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Exclude write-generated version timestamps only during result comparison."""
    result = json.loads(json.dumps(snapshot, allow_nan=False))
    for key in ("core", "record"):
        result.get(key, {}).pop("updated_at", None)
    return result


def expected_snapshot(plan: dict[str, Any]) -> dict[str, Any]:
    """Apply only the approved changes to a copy of the reviewed guard fields."""
    result = comparable(plan["before"])
    kind = plan["draft"]["kind"]
    record = result["core" if kind in CORE_EDIT_ENDPOINTS else "record"]
    record.update(plan["body"])
    if kind in CORE_EDIT_ENDPOINTS and "name" in plan["body"]:
        result["owner"]["name"] = plan["body"]["name"]
    if kind == "tag":
        old = plan["before"]["record"]["tag_id"]
        new = plan["body"]["tag_id"]
        result["core"]["tags"] = sorted(
            new if uid == old else uid for uid in result["core"]["tags"]
        )
    return result


class EditService:
    """A finite writer separate from the GET client and creation executor.

    Each proposal edits one supported core entity or exact character attachment.
    A pre-send read detects observed stale data, but is not an upstream lock.
    """

    def __init__(
        self,
        reader: KankaClient,
        token: str,
        store: EditStore,
        campaign_ids: frozenset[int],
        operations: frozenset[str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        from loremaster.edit_models import EDIT_OPERATIONS

        if (
            not token.strip()
            or not campaign_ids
            or not operations
            or not operations <= EDIT_OPERATIONS
        ):
            raise ValueError(
                "Edits need an explicit token, campaigns and edit operations"
            )
        for cid in campaign_ids:
            reader._check_campaign(cid)
        self.reader, self.store = reader, store
        self.campaign_ids, self.operations = (
            frozenset(campaign_ids),
            frozenset(operations),
        )
        self._lock = asyncio.Lock()
        self._writer = httpx.AsyncClient(
            base_url=API_BASE_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token.strip()}",
            },
            timeout=15,
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Release writer and storage; the server owns the shared GET client."""
        await self._writer.aclose()
        self.store.close()

    def _scope(self, draft: EditDraft) -> None:
        self.reader._check_campaign(draft.campaign_id)
        if (
            draft.campaign_id not in self.campaign_ids
            or draft.kind + ".update" not in self.operations
        ):
            raise PermissionError(
                "Edit is outside the configured campaign/operation scope"
            )

    def _visibility(self, draft: EditDraft, before: dict[str, Any]) -> None:
        """Leave visibility intact and reject admin-only PATCHes for non-admins."""
        for record in (before["core"], before.get("record", {})):
            self.reader.require_write_visibility(
                draft.campaign_id,
                is_private=record.get("is_private") is True,
                visibility_id=record.get("visibility_id")
                or (2 if record.get("visibility") == "admin" else None),
            )

    async def _references(self, draft: EditDraft) -> dict[str, Any]:
        result = {}
        for field, value in draft.changes.items():
            if field not in REFERENCE_FIELDS:
                continue
            _body_key, expected = REFERENCE_FIELDS[field]
            for uid in value if isinstance(value, list) else [value]:
                if expected is None:
                    entity = await self.reader.get_entity_metadata(
                        draft.campaign_id, uid
                    )
                else:
                    entity = (await self.reader.get_entity(draft.campaign_id, uid))[
                        "entity"
                    ]
                    if entity["entity_type"] != expected:
                        raise ValueError(f"Edit reference must identify a {expected}")
                if not positive(entity.get("child_id")):
                    raise KankaError("Edit reference has an invalid module ID")
                result[str(uid)] = {
                    key: entity.get(key)
                    for key in ("id", "campaign_id", "child_id", "entity_type", "name")
                }
        return result

    async def _linked_item(self, cid: int, module_id: Any) -> None:
        if module_id is None:
            return
        if not positive(module_id):
            raise KankaError("Invalid existing inventory item ID")
        item = (await self.reader._get(f"campaigns/{cid}/items/{module_id}"))["data"]
        if (
            not isinstance(item, dict)
            or not positive(item.get("entity_id"))
            or type(item.get("id")) is not int
            or item["id"] != module_id
        ):
            raise KankaError("Invalid existing inventory item")
        verified = await self.reader.get_entity(cid, item["entity_id"])
        if (
            verified["entity"]["entity_type"] != "item"
            or verified["details"]["id"] != module_id
        ):
            raise KankaError("Mismatched existing inventory item")

    async def _snapshot(self, draft: EditDraft) -> dict[str, Any]:
        self._scope(draft)
        cid, uid = draft.campaign_id, draft.entity_id
        campaign = (await self.reader._get(f"campaigns/{cid}"))["data"]
        if (
            not isinstance(campaign, dict)
            or type(campaign.get("id")) is not int
            or campaign["id"] != cid
            or not isinstance(campaign.get("name"), str)
        ):
            raise KankaError("Invalid edit campaign identity")
        owner = await self.reader.get_entity(cid, uid)
        expected_type = (
            draft.kind
            if draft.kind in CORE_EDIT_ENDPOINTS
            else None
            if draft.kind == "post"
            else "character"
        )
        if (
            expected_type is not None
            and owner["entity"]["entity_type"] != expected_type
        ):
            raise ValueError(f"Edit owner must identify a {expected_type}")
        details = owner["details"]
        if (
            type(details.get("is_private")) is not bool
            or "entry" not in details
            or details["entry"] is not None
            and not isinstance(details["entry"], str)
        ):
            raise KankaError("Entity baseline lacks description/privacy")
        core = {key: details[key] for key in CORE_FIELDS if key in details}
        if owner["entity"]["entity_type"] in (
            "family",
            "quest",
            "map",
            "journal",
            "note",
        ):
            core.update(
                {key: details[key] for key in LORE_GUARD_FIELDS if key in details}
            )
        if any(
            core.get(key) is not None and not isinstance(core[key], str)
            for key in ("age", "sex", "updated_at")
        ):
            raise KankaError("Invalid character text/version baseline")
        for field in (
            "races",
            "locations",
            "tags",
            "families",
            "private_races",
            "private_locations",
            "private_families",
        ):
            if field in core:
                values = core[field]
                if (
                    not isinstance(values, list)
                    or any(not positive(value) for value in values)
                    or len(set(values)) != len(values)
                ):
                    raise KankaError("Invalid character reference baseline")
                core[field] = sorted(values)
        result = {
            "campaign": {"id": cid, "name": campaign["name"]},
            "owner": {
                key: owner["entity"].get(key)
                for key in ("id", "child_id", "entity_type", "name")
            },
            "core": core,
        }
        if draft.kind in CORE_EDIT_ENDPOINTS:
            for field in draft.changes:
                if field in REFERENCE_FIELDS and REFERENCE_FIELDS[field][0] not in core:
                    raise KankaError(
                        "Cannot replace an unavailable reference collection"
                    )
            return result
        section = {
            "post": "posts",
            "inventory": "inventory",
            "relation": "relations",
            "tag": "entity_tags",
        }[draft.kind]
        base = f"campaigns/{cid}/entities/{uid}/{section}"
        if draft.kind == "inventory":
            rows = await self.reader._list_rows(base)
            if len({row["id"] for row in rows}) != len(rows):
                raise KankaError("Duplicate inventory record IDs")
            if any(
                type(row.get("entity_id")) is not int or row["entity_id"] != uid
                for row in rows
            ):
                raise KankaError("Inventory ownership mismatch")
            matches = [row for row in rows if row["id"] == draft.record_id]
            if len(matches) != 1:
                raise KankaError("Selected inventory record is unavailable")
            record = matches[0]
        else:
            record = (await self.reader._get(f"{base}/{draft.record_id}"))["data"]
            if draft.kind == "tag" and isinstance(record, dict) and "id" not in record:
                rows = await self.reader._list_rows(base)
                if len({row["id"] for row in rows}) != len(rows):
                    raise KankaError("Duplicate tag assignment record IDs")
                matches = [row for row in rows if row["id"] == draft.record_id]
                if len(matches) != 1 or self._tag_id(
                    record.get("tag_id")
                ) != self._tag_id(matches[0].get("tag_id")):
                    raise KankaError(
                        "Tag detail does not match the selected assignment"
                    )
                self._record_identity(draft, record | {"id": draft.record_id})
                record = matches[0] | record
        self._record_identity(draft, record)
        if draft.kind in ("post", "relation"):
            text_fields = ("name", "entry") if draft.kind == "post" else ("relation",)
            visibility = record.get("visibility_id")
            if (
                any(not isinstance(record.get(key), str) for key in text_fields)
                or type(visibility) is not int
                or not 1 <= visibility <= 5
            ):
                raise KankaError("Selected attachment lacks text/visibility baseline")
        filtered = {
            key: record[key] for key in RECORD_FIELDS[draft.kind] if key in record
        }
        if record.get("updated_at") is not None and not isinstance(
            record["updated_at"], str
        ):
            raise KankaError("Invalid attachment version baseline")
        if draft.kind == "inventory":
            if type(record.get("is_equipped")) is not bool:
                raise KankaError("Invalid inventory equipped baseline")
            if any(
                record.get(key) is not None and not isinstance(record[key], str)
                for key in ("name", "position")
            ):
                raise KankaError("Invalid inventory text baseline")
            visibility = record.get("visibility_id")
            if not (
                type(record.get("is_private")) is bool
                or type(visibility) is int
                and 1 <= visibility <= 5
                or record.get("visibility") in ("all", "admin", "self")
            ):
                raise KankaError("Inventory visibility baseline is unavailable")
            try:
                amount = record.get("amount")
                if type(amount) not in (int, float, str) or len(str(amount)) > 4096:
                    raise ValueError("Invalid amount")
                number = Decimal(str(amount))
                if (
                    not number.is_finite()
                    or number <= 0
                    or abs(number.adjusted()) > 4096
                ):
                    raise ValueError("Invalid amount")
                # Avoid context-dependent rounding and unbounded exponent expansion.
                quantity = format(number, "f")
                filtered["amount"] = (
                    quantity.rstrip("0").rstrip(".") if "." in quantity else quantity
                )
            except (ValueError, InvalidOperation):
                raise KankaError("Invalid inventory amount baseline") from None
            if record.get("item_id") is None and not record.get("name"):
                raise KankaError("Inventory entry has neither a linked item nor a name")
            await self._linked_item(cid, record.get("item_id"))
        elif draft.kind == "relation":
            attitude = record.get("attitude")
            if attitude is not None and (
                type(attitude) is not int or not -100 <= attitude <= 100
            ):
                raise KankaError("Invalid relation attitude baseline")
            if "is_pinned" in record and type(record["is_pinned"]) is not bool:
                raise KankaError("Invalid relation pinned baseline")
            await self.reader.get_entity_metadata(cid, record["target_id"])
        elif draft.kind == "tag":
            filtered["tag_id"] = self._tag_id(record.get("tag_id"))
            if "tags" not in core or filtered["tag_id"] not in core["tags"]:
                raise KankaError("Tag assignment is inconsistent with character tags")
        result["record"] = filtered
        return result

    @staticmethod
    def _tag_id(value: Any) -> int:
        if (
            isinstance(value, str)
            and value.isascii()
            and value.isdecimal()
            and len(value) <= 18
        ):
            value = int(value)
        if not positive(value):
            raise KankaError("Invalid tag assignment ID")
        return value

    @staticmethod
    def _record_identity(draft: EditDraft, record: Any) -> None:
        if (
            not isinstance(record, dict)
            or type(record.get("id")) is not int
            or record["id"] != draft.record_id
        ):
            raise KankaError("Selected attachment record ID mismatch")
        cid = record.get("campaign_id", draft.campaign_id)
        if type(cid) is not int or cid != draft.campaign_id:
            raise KankaError("Selected attachment campaign mismatch")
        owner = "owner_id" if draft.kind == "relation" else "entity_id"
        if (draft.kind != "tag" or owner in record) and (
            type(record.get(owner)) is not int or record[owner] != draft.entity_id
        ):
            raise KankaError(
                "Selected attachment owner mismatch; "
                "incoming edges are not editable here"
            )
        if draft.kind == "relation" and not positive(record.get("target_id")):
            raise KankaError("Invalid relation target")

    async def _rename(self, draft: EditDraft) -> None:
        if draft.kind in CORE_EDIT_ENDPOINTS and "name" in draft.changes:
            candidates = await self.reader.find_entities_by_name(
                draft.campaign_id, draft.changes["name"]
            )
            if any(row["entity_id"] != draft.entity_id for row in candidates["exact"]):
                raise ValueError("The proposed name conflicts with an existing entity")

    async def prepare(
        self,
        draft: EditDraft,
        *,
        proposal_id: str | None = None,
        expected_before: dict[str, Any] | None = None,
        expected_owner: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read the exact target/references and persist an unapproved minimal diff."""
        draft = EditDraft.model_validate(draft.model_dump())
        self._scope(draft)
        if proposal_id is not None:
            previous = self.store.existing(proposal_id)
            if previous is not None:
                if (
                    previous["plan"]["draft"] != draft.model_dump(mode="json")
                    or previous["plan"].get("provenance") != provenance
                ):
                    raise ValueError("Preparation ID is bound to different content")
                return previous
        async with self.reader.account_reads():
            before = await self._snapshot(draft)
            self._visibility(draft, before)
            record = before["core" if draft.kind in CORE_EDIT_ENDPOINTS else "record"]
            for actual, baseline in (
                (record, expected_before),
                (before["core"], expected_owner),
            ):
                for field, expected in (baseline or {}).items():
                    observed = actual.get(field)
                    if field == "amount" and type(expected) in (int, float, str):
                        try:
                            equal = Decimal(str(observed)) == Decimal(str(expected))
                        except InvalidOperation:
                            equal = False
                    else:
                        equal = (
                            type(observed) is type(expected) and observed == expected
                        )
                    if field not in actual or not equal:
                        raise ValueError(
                            "note_evidence_stale: edit baseline changed; "
                            "refresh analysis"
                        )
            refs = await self._references(draft)
            await self._rename(draft)
        plan = build_edit_plan(draft, before, refs)
        if provenance is not None:
            plan["provenance"] = provenance
        if draft.kind == "tag" and plan["body"]["tag_id"] in before["core"]["tags"]:
            raise ValueError("The replacement tag is already assigned")
        return self.store.prepare(plan, proposal_id=proposal_id)

    def status(self, campaign_id: int, proposal_id: str) -> dict[str, Any]:
        """Read scoped local status without exposing the private baseline/diff."""
        review = self.store.review(proposal_id)
        draft = EditDraft.model_validate(review["plan"]["draft"])
        self._scope(draft)
        if type(campaign_id) is not int or draft.campaign_id != campaign_id:
            raise PermissionError("Edit belongs to a different campaign")
        return {
            key: review[key]
            for key in (
                "proposal_id",
                "status",
                "outcome",
                "approval_expires_at",
                "reviewed_by",
                "reconciled_by",
            )
        } | {
            "entity_id": draft.entity_id,
            "kind": draft.kind,
            "record_id": draft.record_id,
            "diagnostics": review["diagnostics"],
        }

    async def _verify(self, draft: EditDraft, plan: dict[str, Any]) -> None:
        actual = await self._snapshot(draft)
        observed, expected = comparable(actual), expected_snapshot(plan)
        if "entry" in plan["body"]:
            section = "core" if draft.kind in CORE_EDIT_ENDPOINTS else "record"
            if entry_matches(plan["body"]["entry"], observed[section].get("entry")):
                observed[section]["entry"] = plan["body"]["entry"]
        if observed != expected:
            raise KankaError(
                "Edited record differs from the approved result "
                "or untouched guard fields",
                code="edit_verification_failed",
            )
        if await self._references(draft) != plan["references"]:
            raise KankaError(
                "Edit reference identities changed", code="edit_verification_failed"
            )

    async def execute(self, campaign_id: int, proposal_id: str) -> dict[str, Any]:
        """Consume local approval; send at most one PATCH and independently verify."""
        async with self._lock:
            self.status(campaign_id, proposal_id)
            try:
                grant = self.store.claim(proposal_id)
            except PermissionError as error:
                raise KankaError(str(error), code="approval_required") from None
            review = self.store.review(proposal_id)
            plan = review["plan"]
            draft = EditDraft.model_validate(plan["draft"])
            try:
                async with self.reader.account_reads():
                    if review["outcome"] != "confirmed":
                        refs = await self._references(draft)
                        await self._rename(draft)
                        current = await self._snapshot(draft)
                        if current != plan["before"] or refs != plan["references"]:
                            self.store.stale(proposal_id, grant)
                            raise KankaError(
                                "The reviewed edit baseline changed; "
                                "prepare a new proposal",
                                code="stale_proposal",
                            )
                        budget = self.reader._budget.get()
                        if budget is None:
                            raise RuntimeError("Edit request budget is unavailable")
                        await verify_publication_sources(
                            self.reader, draft.campaign_id, plan.get("provenance")
                        )
                        await self.reader.reserve_request()
                        self._scope(draft)
                        self._visibility(draft, current)
                        self.store.begin(proposal_id, grant)
                        await self._send(draft, proposal_id, plan, grant)
                    await self._verify(draft, plan)
                    self.store.complete(proposal_id, grant)
                return {
                    "proposal_id": proposal_id,
                    "entity_id": draft.entity_id,
                    "record_id": draft.record_id,
                    "status": "verified",
                    "source": API_BASE_URL + plan["path"],
                }
            except asyncio.CancelledError:
                raise
            except (
                KankaError,
                PermissionError,
                ValueError,
                RuntimeError,
                sqlite3.Error,
            ) as error:
                status = self.status(campaign_id, proposal_id)
                return {
                    **status,
                    "status": "uncertain"
                    if status["outcome"] in ("intent", "uncertain")
                    else "stale"
                    if status["status"] == "stale"
                    else "partial"
                    if status["outcome"] == "confirmed"
                    else "failed",
                    "error": {
                        "code": error.code
                        if isinstance(error, KankaError)
                        else "edit_stopped",
                        "message": str(error)
                        if isinstance(error, (KankaError, PermissionError, ValueError))
                        else "Edit stopped; inspect durable status",
                        "status_code": error.status_code
                        if isinstance(error, KankaError)
                        else None,
                        "retry_after_seconds": error.retry_after_seconds
                        if isinstance(error, KankaError)
                        else None,
                    },
                }

    async def _send(
        self, draft: EditDraft, proposal_id: str, plan: dict[str, Any], grant: str
    ) -> None:
        record_receipt = partial(
            self.store.journal.record_receipt, proposal_id, "patch", grant
        )
        receipt: dict[str, Any] = {"stage": "request"}
        try:
            response, payload, receipt = await send_write(
                self._writer,
                "PATCH",
                plan["path"],
                plan["body"],
                record_receipt,
                self.reader.observe_response,
            )
            if not 200 <= response.status_code < 300:
                self.store.finish(
                    proposal_id,
                    "rejected"
                    if response.status_code in KNOWN_REJECTIONS
                    else "uncertain",
                )
                raise KankaError(
                    f"Kanka returned HTTP {response.status_code}; inspect edit status",
                    code="upstream_http",
                    status_code=response.status_code,
                    retry_after_seconds=receipt.get("retry_after_seconds"),
                )
            if receipt.get("failure"):
                raise ValueError("Invalid edit response")
            record = payload["data"]
            receipt["stage"] = "identity_validation"
            record_receipt(receipt)
            if draft.kind in CORE_EDIT_ENDPOINTS:
                if (
                    not isinstance(record, dict)
                    or type(record.get("id")) is not int
                    or record["id"] != plan["before"]["core"]["id"]
                    or type(record.get("entity_id")) is not int
                    or record["entity_id"] != draft.entity_id
                    or (
                        "campaign_id" in record
                        and (
                            type(record["campaign_id"]) is not int
                            or record["campaign_id"] != draft.campaign_id
                        )
                    )
                ):
                    raise ValueError("Mismatched entity edit response")
            else:
                if (
                    draft.kind == "tag"
                    and isinstance(record, dict)
                    and "id" not in record
                ):
                    # Live tag responses omit the ID; read-back also checks the list.
                    if self._tag_id(record.get("tag_id")) != plan["body"]["tag_id"]:
                        raise ValueError("Mismatched tag edit response")
                    record = record | {"id": draft.record_id}
                self._record_identity(draft, record)
            receipt["stage"] = "identity_verified"
            record_receipt(receipt)
        except asyncio.CancelledError:
            self.store.finish(proposal_id, "uncertain")
            raise
        except (httpx.RequestError, ValueError, KeyError, TypeError):
            if not receipt.get("failure"):
                receipt.update(stage="identity_validation", failure="invalid_identity")
                record_receipt(receipt)
            self.store.finish(proposal_id, "uncertain")
            raise KankaError(
                f"Edit response is uncertain ({receipt['failure']}); "
                "inspect diagnostics",
                code="uncertain_result",
                status_code=receipt.get("http_status"),
            ) from None
        except KankaError:
            if self.store.review(proposal_id)["outcome"] == "intent":
                if receipt.get("stage") == "identity_validation":
                    receipt["failure"] = "invalid_identity"
                    record_receipt(receipt)
                self.store.finish(proposal_id, "uncertain")
            raise
        self.store.finish(proposal_id, "confirmed")

    async def inspect_reconciliation(
        self, campaign_id: int, proposal_id: str
    ) -> dict[str, Any]:
        """GET-only evidence for the same uncertain target, never permission to send."""
        self.status(campaign_id, proposal_id)
        review = self.store.review(proposal_id)
        if review["outcome"] not in ("intent", "uncertain"):
            raise PermissionError("Edit has no uncertain intent")
        draft = EditDraft.model_validate(review["plan"]["draft"])
        async with self.reader.account_reads():
            await self._verify(draft, review["plan"])
        return {
            "proposal_id": proposal_id,
            "fingerprint": review["review_digest"],
            "entity_id": draft.entity_id,
            "record_id": draft.record_id,
            "method": "PATCH",
            "source": API_BASE_URL + review["plan"]["path"],
            "matches_requested_result": True,
            "origin_verified": False,
        }
