"""Opt-in supervised entity creation with durable intent and read-back."""

import asyncio
import sqlite3
from functools import partial
from typing import Any
from unicodedata import normalize

import httpx

from loremaster.aggregate import get_entity_full
from loremaster.approvals import LocalApprovals
from loremaster.creation import (
    CREATION_ENDPOINTS,
    ApprovedCreation,
    CharacterDraft,
    CreationDraft,
    CreationScope,
    EntityPostDraft,
    OrganisationDraft,
    creation_kind,
    preview_creation,
    referenced_entities,
    resolve_creation_request,
    validate_creation_draft,
)
from loremaster.kanka import API_BASE_URL, KankaClient, KankaError
from loremaster.publication import verify_publication_sources
from loremaster.verification import (
    creation_details,
    matches_creation_fields,
    verify_created,
    verify_result_ids,
)
from loremaster.write_diagnostics import send_write

KNOWN_REJECTIONS = {400, 401, 403, 404, 409, 422, 429}


class CreationService:
    """The separate writer; construction requires explicit host scope and storage.

    KankaClient stays GET-only. This writer only sends requests derived from an
    immutable approved draft, never caller-supplied paths or arbitrary JSON.
    The server owns reader shutdown; aclose releases writer and local storage.
    """

    def __init__(
        self,
        reader: KankaClient,
        token: str,
        store: LocalApprovals,
        scope: CreationScope,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not scope.campaign_ids or not scope.operations:
            raise ValueError(
                "Creation requires explicit campaigns and operation classes"
            )
        for campaign_id in scope.campaign_ids:
            reader._check_campaign(campaign_id)
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Creation requires a non-empty API token")
        self.reader, self.store = reader, store
        self.scope = CreationScope.model_validate(scope.model_dump())
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
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        """Release the opt-in writer and its approval/progress store."""
        await self._writer.aclose()
        self.store.close()

    async def _references(self, draft: CreationDraft) -> dict[str, Any]:
        self.scope.check(draft)
        self._visibility(draft)
        campaign = (await self.reader._get(f"campaigns/{draft.campaign_id}"))["data"]
        if (
            not isinstance(campaign, dict)
            or type(campaign.get("id")) is not int
            or campaign["id"] != draft.campaign_id
            or not isinstance(campaign.get("name"), str)
        ):
            raise KankaError("Invalid campaign identity")
        result: dict[str, Any] = {
            "campaign": {"id": campaign["id"], "name": campaign["name"]},
            "entities": {},
            "item_ids": {},
        }
        references = referenced_entities(draft)
        for uid in dict.fromkeys(uid for uid, _expected in references):
            record = await self.reader.get_entity_metadata(draft.campaign_id, uid)
            result["entities"][str(uid)] = {
                key: record.get(key)
                for key in ("id", "campaign_id", "child_id", "entity_type", "name")
            }
        for uid, expected in dict.fromkeys(references):
            if expected is None:
                continue
            linked = await self.reader.get_entity(draft.campaign_id, uid)
            if linked["entity"]["entity_type"] != expected:
                raise ValueError(f"Creation reference must identify a {expected}")
            if expected == "item":
                result["item_ids"][str(uid)] = linked["details"]["id"]
        return result

    async def _name_exists(self, draft: CreationDraft) -> bool:
        """Posts have an owner-local namespace; core entities use campaign lookup."""
        if isinstance(draft, EntityPostDraft):
            rows = await self.reader._list_rows(
                f"campaigns/{draft.campaign_id}/entities/{draft.entity_id}/posts"
            )
            if len({row["id"] for row in rows}) != len(rows) or any(
                type(row.get("entity_id")) is not int
                or row["entity_id"] != draft.entity_id
                or not isinstance(row.get("name"), str)
                for row in rows
            ):
                raise KankaError("Invalid post collection identity")
            name = normalize("NFC", draft.name).casefold()
            return any(
                normalize("NFC", row["name"].strip()).casefold() == name for row in rows
            )
        return bool(
            (await self.reader.find_entities_by_name(draft.campaign_id, draft.name))[
                "exact"
            ]
        )

    async def prepare(
        self,
        draft: CreationDraft,
        *,
        proposal_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve references and save a proposal; issue no approval or POST."""
        draft = validate_creation_draft(draft)
        self.scope.check(draft)
        if proposal_id is not None:
            previous = self.store.existing(proposal_id)
            if previous is not None:
                if (
                    self.store.draft(proposal_id) != draft
                    or previous["provenance"] != provenance
                ):
                    raise ValueError("Preparation ID is bound to different content")
                return previous
        self._visibility(draft)
        async with self.reader.account_reads():
            refs = await self._references(draft)
            if await self._name_exists(draft):
                raise ValueError(
                    "Entity name already exists; resolve the conflict first"
                )
        return self.store.prepare(
            draft, refs, proposal_id=proposal_id, provenance=provenance
        )

    def _visibility(self, draft: CreationDraft) -> None:
        """Check every core/attachment before preparing or sending any write."""
        for operation in preview_creation(draft)["operations"]:
            body = operation["body"]
            self.reader.require_write_visibility(
                draft.campaign_id,
                is_private=body.get("is_private", False),
                visibility_id=body.get("visibility_id"),
            )

    def status(self, campaign_id: int, proposal_id: str) -> dict[str, Any]:
        """Read scoped proposal progress; do not return another campaign's draft."""
        draft = self.store.draft(proposal_id)
        self.scope.check(draft)
        if draft.campaign_id != campaign_id:
            raise PermissionError("Proposal belongs to a different campaign")
        review = self.store.review(proposal_id)
        return {
            key: review[key]
            for key in (
                "proposal_id",
                "entity_type",
                "status",
                "progress",
                "steps",
                "approval_expires_at",
                "reviewed_by",
            )
        } | {"diagnostics": self.store.journal.receipts(proposal_id)}

    async def execute(self, campaign_id: int, proposal_id: str) -> dict[str, Any]:
        """Claim local approval once and execute only its stored, scoped draft."""
        async with self._lock:
            self.status(campaign_id, proposal_id)
            kind = creation_kind(self.store.draft(proposal_id))
            try:
                self._active_step = "preflight"
                approved = self.store.claim(proposal_id)
            except PermissionError as error:
                raise KankaError(str(error), code="approval_required") from None
            try:
                if "uncertain" in self.store.journal.progress(proposal_id):
                    raise KankaError(
                        "Uncertain intent requires reviewed reconciliation",
                        code="reconciliation_required",
                    )
                self._visibility(approved.draft)
                async with self.reader.account_reads():
                    return await self._execute(proposal_id, approved)
            except asyncio.CancelledError:
                raise
            except (
                KankaError,
                PermissionError,
                ValueError,
                RuntimeError,
                sqlite3.Error,
            ) as error:
                progress = self.store.journal.progress(proposal_id)
                status = (
                    "uncertain"
                    if "uncertain" in progress
                    else "partial"
                    if progress
                    else "failed"
                )
                return {
                    "proposal_id": proposal_id,
                    "entity_type": kind,
                    "status": status,
                    "entity_id": progress.get(kind, {}).get("entity_id"),
                    "completed": [key for key in progress if key != "uncertain"],
                    "failed_step": progress.get("uncertain")
                    or next(
                        (
                            key
                            for key, state in self.store.journal.states(
                                proposal_id
                            ).items()
                            if state != "confirmed"
                        ),
                        self._active_step,
                    ),
                    "error": {
                        "code": error.code
                        if isinstance(error, KankaError)
                        else "approval_denied"
                        if isinstance(error, PermissionError)
                        else "creation_stopped",
                        "message": str(error)
                        if isinstance(error, (KankaError, PermissionError, ValueError))
                        else "Creation stopped; inspect durable progress",
                        "status_code": error.status_code
                        if isinstance(error, KankaError)
                        else None,
                        "retry_after_seconds": error.retry_after_seconds
                        if isinstance(error, KankaError)
                        else None,
                    },
                    "diagnostics": self.store.journal.receipts(proposal_id),
                }

    async def _execute(
        self, proposal_id: str, approved: ApprovedCreation
    ) -> dict[str, Any]:
        draft = approved.draft
        kind = creation_kind(draft)
        journal = self.store.journal
        progress = journal.progress(proposal_id)
        if "uncertain" in progress:
            raise KankaError(
                "Uncertain intent requires reviewed reconciliation",
                code="reconciliation_required",
            )
        refs = await self._references(draft)
        if refs != self.store.review(proposal_id)["references"]:
            raise ValueError("Referenced identities changed after review")
        module_ids = {
            uid: refs["entities"][str(uid)]["child_id"]
            for uid, expected in referenced_entities(draft)
            if expected is not None
        }
        journal.bind(proposal_id, approved, module_ids)
        uid = progress.get(kind, {}).get("entity_id")
        if uid is None:
            if await self._name_exists(draft):
                raise ValueError("Entity name already exists; no creation was sent")
        else:
            await self._verify(draft, progress, module_ids)
        for operation in preview_creation(draft)["operations"]:
            key = operation["key"]
            if key in progress:
                continue
            self._active_step = key
            path, body = resolve_creation_request(
                draft,
                key,
                entity_id=uid,
                child_id=progress.get(kind, {}).get("id"),
                module_ids=module_ids,
            )
            budget = self.reader._budget.get()
            if budget is None:
                raise RuntimeError("Creation requires an account request budget")
            await verify_publication_sources(
                self.reader,
                draft.campaign_id,
                self.store.review(proposal_id).get("provenance"),
            )
            await self.reader.reserve_request()
            self.scope.check(draft)
            self._visibility(draft)
            self.store.check(proposal_id, approved)
            journal.begin(proposal_id, approved, key, path, body)
            record_receipt = partial(
                journal.record_receipt, proposal_id, key, approved.proposal_id
            )
            receipt: dict[str, Any] = {"stage": "request"}
            try:
                response, payload, receipt = await send_write(
                    self._writer,
                    "POST",
                    path,
                    body,
                    record_receipt,
                    self.reader.observe_response,
                )
                if not 200 <= response.status_code < 300:
                    journal.finish(
                        proposal_id,
                        key,
                        "rejected"
                        if response.status_code in KNOWN_REJECTIONS
                        else "uncertain",
                    )
                    raise KankaError(
                        f"Kanka returned HTTP {response.status_code}; "
                        "retry requires review",
                        code="upstream_http",
                        status_code=response.status_code,
                        retry_after_seconds=receipt.get("retry_after_seconds"),
                    )
                if receipt.get("failure"):
                    raise ValueError("Invalid creation response")
                data = payload["data"]
                if (
                    isinstance(data, list)
                    and key.startswith("relation:")
                    and len(data) == 1
                ):
                    data = data[0]
                if (
                    key == kind
                    and not isinstance(draft, EntityPostDraft)
                    and isinstance(data, dict)
                    and type(data.get("id")) is int
                    and data["id"] > 0
                    and data.get("entity_id") is None
                ):
                    receipt["stage"] = "identity_readback"
                    record_receipt(receipt)
                    core = await self.reader.get_entity_by_child(
                        draft.campaign_id, kind, data["id"]
                    )
                    if not matches_creation_fields(body, creation_details(draft, core)):
                        raise ValueError(
                            "Response module ID differs from the approved entity"
                        )
                    data = core["details"]
                    receipt.update(
                        stage="identity_verified",
                        candidate_ids={
                            "id": data["id"],
                            "entity_id": data["entity_id"],
                        },
                    )
                    record_receipt(receipt)
                owner = (
                    "organisation_id"
                    if key.startswith("member:")
                    else "owner_id"
                    if key.startswith("relation:")
                    else "entity_id"
                )
                expected_owner = (
                    progress.get(kind, {}).get("id")
                    if key.startswith("member:")
                    else uid
                )
                if (
                    not isinstance(data, dict)
                    or type(data.get("id")) is not int
                    or data["id"] <= 0
                    or type(data.get(owner)) is not int
                    or data[owner] <= 0
                    or (key != kind and data[owner] != expected_owner)
                    or (
                        isinstance(draft, EntityPostDraft)
                        and data["entity_id"] != draft.entity_id
                    )
                    or (
                        key.startswith("member:")
                        and (
                            type(data.get("character_id")) is not int
                            or data["character_id"] != body["character_id"]
                        )
                    )
                ):
                    receipt.update(
                        stage="identity_validation", failure="invalid_identity"
                    )
                    record_receipt(receipt)
                    raise ValueError("Unverifiable creation identity")
                receipt["stage"] = "identity_verified"
                record_receipt(receipt)
            except asyncio.CancelledError:
                journal.finish(proposal_id, key, "uncertain")
                raise
            except (httpx.RequestError, ValueError, KeyError, TypeError):
                if not receipt.get("failure"):
                    receipt.update(
                        stage="identity_validation", failure="invalid_identity"
                    )
                    record_receipt(receipt)
                journal.finish(proposal_id, key, "uncertain")
                raise KankaError(
                    f"Creation response could not be verified "
                    f"({receipt.get('failure', 'invalid_identity')}); "
                    "inspect diagnostics",
                    code="uncertain_result",
                    status_code=receipt.get("http_status"),
                ) from None
            except KankaError:
                if journal.states(proposal_id).get(key) == "intent":
                    if receipt.get("stage") == "identity_readback":
                        receipt["failure"] = "identity_readback_failed"
                        record_receipt(receipt)
                    journal.finish(proposal_id, key, "uncertain")
                raise
            journal.finish(proposal_id, key, "confirmed", data)
            progress[key] = data
            uid = progress[kind]["entity_id"]
        bundle = await self._verify(draft, progress, module_ids)
        self.store.complete(proposal_id, approved)
        return {
            "proposal_id": proposal_id,
            "entity_type": kind,
            "status": "verified",
            "entity_id": uid,
            **(
                {"post_id": progress[kind]["id"]}
                if isinstance(draft, EntityPostDraft)
                else {"child_id": progress[kind]["id"]}
            ),
            "completed": list(progress),
            "sources": bundle["sources"],
            "reviewed_by": approved.approved_by,
        }

    async def _verify(
        self,
        draft: CreationDraft,
        progress: dict[str, Any],
        module_ids: dict[int, int],
    ) -> dict[str, Any]:
        self._active_step = "verification"
        kind = creation_kind(draft)
        uid = progress[kind]["entity_id"]
        if isinstance(draft, EntityPostDraft):
            if uid != draft.entity_id:
                raise KankaError("Created post owner changed")
            await self.reader.get_entity(draft.campaign_id, uid)
            base = f"campaigns/{draft.campaign_id}/entities/{uid}"
            path = f"{base}/posts/{progress[kind]['id']}"
            record = (await self.reader._get(path))["data"]
            if (
                not isinstance(record, dict)
                or type(record.get("id")) is not int
                or record["id"] != progress[kind]["id"]
            ):
                raise KankaError("Created post ID changed")
            bundle = {
                "details": record,
                "sections": {},
                "missing_data": [],
                "sources": {
                    "entity": API_BASE_URL + base,
                    "post": API_BASE_URL + path,
                },
            }
            verify_created(draft, bundle, module_ids)
            return bundle
        if isinstance(draft, OrganisationDraft):
            core = await self.reader.get_entity(draft.campaign_id, uid)
            members = await self.reader.get_organisation_members(draft.campaign_id, uid)
            base = f"{API_BASE_URL}campaigns/{draft.campaign_id}"
            bundle = {
                **core,
                "sections": {"members": members},
                "missing_data": [],
                "sources": {
                    "entity": f"{base}/entities/{uid}",
                    "details": f"{base}/organisations/{progress[kind]['id']}",
                    "members": members["source"],
                },
            }
            partial = draft.model_copy(
                update={
                    "members": tuple(
                        member
                        for index, member in enumerate(draft.members)
                        if f"member:{index}" in progress
                    )
                }
            )
        elif not isinstance(draft, CharacterDraft):
            core = await self.reader.get_entity(draft.campaign_id, uid)
            base = f"{API_BASE_URL}campaigns/{draft.campaign_id}"
            bundle = {
                **core,
                "sections": {},
                "missing_data": [],
                "sources": {
                    "entity": f"{base}/entities/{uid}",
                    "details": f"{base}/{CREATION_ENDPOINTS[kind]}/"
                    f"{progress[kind]['id']}",
                },
            }
            partial = draft
        else:
            bundle = await get_entity_full(
                self.reader,
                draft.campaign_id,
                uid,
                sections=["posts", "inventory", "relations"],
            )
            partial = draft.model_copy(
                update={
                    section: tuple(
                        value
                        for index, value in enumerate(getattr(draft, section))
                        if f"{prefix}:{index}" in progress
                    )
                    for section, prefix in (
                        ("posts", "post"),
                        ("inventory", "inventory"),
                        ("relations", "relation"),
                    )
                }
            )
        if (
            bundle["entity"]["entity_type"] != kind
            or bundle["entity"]["child_id"] != progress[kind]["id"]
        ):
            raise KankaError("Previously created entity identity changed")
        verify_created(partial, bundle, module_ids)
        verify_result_ids(progress, bundle)
        return bundle

    async def inspect_reconciliation(
        self, campaign_id: int, proposal_id: str, candidate_id: int
    ) -> dict[str, Any]:
        """Read evidence for a local reviewer; never infer a candidate's origin."""
        self.status(campaign_id, proposal_id)
        self.reader._check_id(candidate_id, "candidate_id")
        pending = self.store.journal.pending(proposal_id)
        if pending["campaign_id"] != campaign_id:
            raise PermissionError("Pending operation belongs to a different campaign")
        body, key = pending["body"], pending["key"]
        kind = creation_kind(self.store.draft(proposal_id))
        async with self.reader.account_reads():
            draft = self.store.draft(proposal_id)
            if isinstance(draft, EntityPostDraft):
                expected_path, _body = resolve_creation_request(draft, kind)
                if pending["path"] != expected_path:
                    raise ValueError("Invalid pending post path")
                record = (await self.reader._get(f"{expected_path}/{candidate_id}"))[
                    "data"
                ]
                candidates = (
                    [record]
                    if isinstance(record, dict)
                    and type(record.get("id")) is int
                    and record["id"] == candidate_id
                    else []
                )
            elif key == kind:
                if (
                    pending["path"]
                    != f"campaigns/{campaign_id}/{CREATION_ENDPOINTS[kind]}"
                ):
                    raise ValueError("Invalid pending entity path")
                core = await self.reader.get_entity(campaign_id, candidate_id)
                candidates = (
                    [creation_details(draft, core)]
                    if core["entity"]["entity_type"] == kind
                    else []
                )
            elif key.startswith("member:"):
                uid = self.store.journal.progress(proposal_id)[kind]["entity_id"]
                owner = body.get("organisation_id")
                self.reader._check_id(owner, "organisation_id")
                if pending["path"] != (
                    f"campaigns/{campaign_id}/organisations/{owner}"
                    "/organisation_members"
                ):
                    raise ValueError("Invalid pending membership path")
                collection = await self.reader.get_organisation_members(
                    campaign_id, uid
                )
                candidates = [
                    row for row in collection["data"] if row["id"] == candidate_id
                ]
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
                    != f"campaigns/{campaign_id}/entities/{owner}/{section}"
                ):
                    raise ValueError("Invalid pending attachment path")
                bundle = await get_entity_full(
                    self.reader, campaign_id, owner, sections=[section]
                )
                if (
                    bundle["missing_data"]
                    or not bundle["sections"][section]["complete"]
                ):
                    raise KankaError("Reconciliation evidence is incomplete")
                candidates = [
                    row
                    for row in bundle["sections"][section]["data"]
                    if type(row.get("id")) is int and row["id"] == candidate_id
                ]
        if len(candidates) != 1 or not matches_creation_fields(body, candidates[0]):
            raise KankaError("Candidate does not uniquely match the pending request")
        return {
            "proposal_id": proposal_id,
            "key": key,
            "fingerprint": pending["fingerprint"],
            "candidate_id": candidate_id,
            "record": candidates[0],
            "source": API_BASE_URL + pending["path"],
        }

    async def inspect_recovery(
        self, campaign_id: int, proposal_id: str
    ) -> dict[str, Any]:
        """Discover recovery candidates with GETs; never reconcile or resend.

        Empty visible search results do not establish rejection/rollback. Receipt
        IDs are hints that must pass exact campaign/type/field checks and origin
        review through the existing terminal reconciliation command.
        """
        status = self.status(campaign_id, proposal_id)
        if "uncertain" not in status["progress"]:
            return {**status, "recovery": {"next_action": "inspect_local_status"}}
        pending = self.store.journal.pending(proposal_id)
        draft = self.store.draft(proposal_id)
        kind = creation_kind(draft)
        recovery: dict[str, Any] = {
            "next_action": "review_upstream_outcome",
            "search_complete": False,
            "absence_proves_rejection": False,
            "origin_review_required": True,
            "verification_complete": True,
            "candidates": [],
        }
        if pending["key"] != kind or isinstance(draft, EntityPostDraft):
            return {
                **status,
                "recovery": {
                    **recovery,
                    "next_action": "review_exact_attachment_candidate",
                },
            }
        ids: set[int] = set()
        try:
            async with self.reader.account_reads():
                found = await self.reader.find_entities_by_name(campaign_id, draft.name)
                recovery["search"] = found
                ids.update(
                    c["entity_id"] for c in found["exact"] if c["entity_type"] == kind
                )
                for receipt in status["diagnostics"]:
                    hints = receipt["details"].get("candidate_ids", {})
                    if "entity_id" in hints:
                        ids.add(hints["entity_id"])
                    elif "id" in hints:
                        try:
                            core = await self.reader.get_entity_by_child(
                                campaign_id, kind, hints["id"]
                            )
                            ids.add(core["entity"]["id"])
                        except (KankaError, ValueError):
                            recovery["unverified_receipt_hint"] = True
                            recovery["verification_complete"] = False
                if len(ids) > 20:
                    raise KankaError("Recovery candidate budget exceeded")
                for uid in sorted(ids):
                    try:
                        evidence = await self.inspect_reconciliation(
                            campaign_id, proposal_id, uid
                        )
                        recovery["candidates"].append(
                            {
                                "entity_id": uid,
                                "child_id": evidence["record"]["id"],
                                "fields_match": True,
                            }
                        )
                    except (KankaError, ValueError):
                        recovery["verification_complete"] = False
                        recovery["candidates"].append(
                            {"entity_id": uid, "fields_match": False}
                        )
            recovery["search_complete"] = True
            if any(candidate["fields_match"] for candidate in recovery["candidates"]):
                recovery["next_action"] = "review_origin_then_terminal_reconcile"
        except KankaError as error:
            recovery["error"] = {"code": error.code, "status_code": error.status_code}
        return {
            **status,
            "recovery": recovery,
            "complete": recovery["search_complete"]
            and recovery["verification_complete"],
        }
