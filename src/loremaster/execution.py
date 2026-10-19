"""Opt-in supervised entity creation with durable intent and read-back."""

import asyncio
import sqlite3
from typing import Any

import httpx

from loremaster.aggregate import get_entity_full
from loremaster.approvals import LocalApprovals
from loremaster.creation import (
    ApprovedCreation,
    CreationDraft,
    CreationScope,
    ItemDraft,
    OrganisationDraft,
    creation_kind,
    preview_creation,
    referenced_entities,
    resolve_creation_request,
    validate_creation_draft,
)
from loremaster.kanka import API_BASE_URL, KankaClient, KankaError
from loremaster.verification import (
    matches_creation_fields,
    verify_created,
    verify_result_ids,
)

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

    async def prepare(self, draft: CreationDraft) -> dict[str, Any]:
        """Resolve references and save a proposal; issue no approval or POST."""
        draft = validate_creation_draft(draft)
        self.scope.check(draft)
        async with self.reader.account_reads():
            refs = await self._references(draft)
            matches = await self.reader.find_entities_by_name(
                draft.campaign_id, draft.name
            )
            if matches["exact"]:
                raise ValueError(
                    "Entity name already exists; resolve the conflict first"
                )
        return self.store.prepare(draft, refs)

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
        }

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
                    },
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
            matches = await self.reader.find_entities_by_name(
                draft.campaign_id, draft.name
            )
            if matches["exact"]:
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
            await budget.wait()
            self.scope.check(draft)
            self.store.check(proposal_id, approved)
            journal.begin(proposal_id, approved, key, path, body)
            try:
                response = await self._writer.post(path, json=body)
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
                    )
                data = response.json()["data"]
                if (
                    isinstance(data, list)
                    and key.startswith("relation:")
                    and len(data) == 1
                ):
                    data = data[0]
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
                        key.startswith("member:")
                        and (
                            type(data.get("character_id")) is not int
                            or data["character_id"] != body["character_id"]
                        )
                    )
                ):
                    raise ValueError("Unverifiable creation identity")
            except asyncio.CancelledError:
                journal.finish(proposal_id, key, "uncertain")
                raise
            except (httpx.RequestError, ValueError, KeyError, TypeError):
                journal.finish(proposal_id, key, "uncertain")
                raise KankaError(
                    "Creation response could not be verified; do not retry",
                    code="uncertain_result",
                ) from None
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
            "child_id": progress[kind]["id"],
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
        elif isinstance(draft, ItemDraft):
            core = await self.reader.get_entity(draft.campaign_id, uid)
            base = f"{API_BASE_URL}campaigns/{draft.campaign_id}"
            bundle = {
                **core,
                "sections": {},
                "missing_data": [],
                "sources": {
                    "entity": f"{base}/entities/{uid}",
                    "details": f"{base}/items/{progress[kind]['id']}",
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
            if key == kind:
                if pending["path"] != f"campaigns/{campaign_id}/{kind}s":
                    raise ValueError("Invalid pending entity path")
                core = await self.reader.get_entity(campaign_id, candidate_id)
                candidates = (
                    [core["details"]] if core["entity"]["entity_type"] == kind else []
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
