"""Opt-in preparation of selected note operations; never approve or send writes."""

import json
import time
from copy import deepcopy

from loremaster.creation import (
    DRAFT_MODELS,
    EntityPostDraft,
    creation_kind,
    referenced_entities,
    resolve_creation_request,
)
from loremaster.edit_models import REFERENCE_FIELDS, EditDraft
from loremaster.editing import EditService
from loremaster.execution import CreationService
from loremaster.note_store import digest
from loremaster.publication import public_source
from loremaster.session_notes import SessionNoteService
from loremaster.verification import creation_details, matches_creation_fields


class SessionNoteBridge:
    """Hand off immutable sourced operations to separately configured supervisors.

    A durable intent and deterministic preparation ID recover across the two
    private databases. Replays preserve approvals and terminal states. Executors
    alone own sends, uncertainty and reconciliation; no method here executes one.
    """

    def __init__(
        self,
        notes: SessionNoteService,
        creation: CreationService | None = None,
        editing: EditService | None = None,
    ) -> None:
        if creation is None and editing is None:
            raise ValueError("Note handoff requires a configured supervised service")
        if any(s.reader is not notes.client for s in (creation, editing) if s):
            raise ValueError("Note handoff must share the scoped reader")
        self.notes, self.creation, self.editing = notes, creation, editing

    @staticmethod
    def _operation(row: dict, key: str) -> dict:
        matches = [
            op
            for op in row["state"].get("preview", [])
            if op["kind"] == "operation" and op["key"] == key
        ]
        if len(matches) != 1:
            raise ValueError("Select one canonical operation key from the preview")
        return matches[0]

    def _entry(self, row: dict, op: dict) -> dict | None:
        entry = self.notes.store.db.execute(
            "SELECT * FROM note_handoffs WHERE document_id=? AND logical_key=?",
            (row["document_id"], op["logical_key"]),
        ).fetchone()
        return dict(entry) if entry else None

    @staticmethod
    def _dependency_blockers(op: dict, source: dict) -> set[str]:
        """List preview blockers resolved by binding verified prerequisite IDs."""
        blockers = {
            f"Awaiting verified ID and separate review: {key}"
            for key in op["dependencies"]
        }
        blockers.update(
            f"Reference {r['field']} is unresolved"
            for r in op["references"]
            if r["target"]["kind"] == "proposal"
        )
        if (source.get("target") or {}).get("kind") == "proposal":
            blockers.add("Post owner is unresolved")
        return blockers

    def _service(self, kind: str) -> CreationService | EditService:
        service = self.creation if kind == "creation" else self.editing
        if service is None:
            raise PermissionError("Required supervised service is not configured")
        return service

    def _review(self, campaign_id: int, entry: dict) -> dict | None:
        service = self._service(entry["service"])
        review = service.store.existing(entry["proposal_id"])
        if review is None:
            return None
        service.status(campaign_id, entry["proposal_id"])
        draft = (
            service.store.draft(entry["proposal_id"]).model_dump(mode="json")
            if entry["service"] == "creation"
            else review["plan"]["draft"]
        )
        if draft != json.loads(entry["operation"])["draft"]:
            raise PermissionError("Handoff preparation failed content validation")
        provenance = (
            review.get("provenance")
            if entry["service"] == "creation"
            else review["plan"].get("provenance")
        )
        if provenance != self._provenance(entry):
            raise PermissionError("Handoff preparation failed provenance validation")
        return review

    @staticmethod
    def _provenance(entry: dict) -> dict:
        intent = json.loads(entry["operation"])
        op = intent["source"]
        result = {
            "document_id": entry["document_id"],
            "import_id": entry["import_id"],
            "version": entry["version"],
            "preview_digest": entry["preview_digest"],
            "operation_key": op["key"],
            "logical_key": op["logical_key"],
            "source_revision": op["source_revision"],
            "extraction_variant": op["extraction_variant"],
            "source_refs": op["source_refs"],
            "assessments": op["assessments"],
            "dependencies": op["dependencies"],
        }
        if "publication" in op:
            review = {
                key: value
                for key, value in op["publication"].items()
                if key != "review_digest"
            }
            review.update(
                concrete_draft=intent["draft"],
                public_sources=intent["publication_sources"],
            )
            result["publication"] = {**review, "review_digest": digest(review)}
        return result

    def inspect(
        self, campaign_id: int, import_id: str, operation_key: str | None = None
    ) -> dict:
        """Read linkage and historical read-back without GETs or approval grants."""
        row = self.notes._load(campaign_id, import_id)
        if operation_key is None:
            return self._overview(row)
        try:
            current_op = self._operation(row, operation_key)
            entry = self._entry(row, current_op)
        except ValueError:
            current_op = None
            entries = self.notes.store.db.execute(
                "SELECT * FROM note_handoffs WHERE import_id=? "
                "AND json_extract(operation, '$.source.key')=?",
                (import_id, operation_key),
            ).fetchall()
            if len(entries) != 1:
                raise ValueError(
                    "No unique historical handoff for this operation"
                ) from None
            entry = dict(entries[0])
        review = self._review(campaign_id, entry) if entry else None
        return {
            "import_id": import_id,
            "complete": True,
            "operation_key": operation_key,
            "proposal_id": entry["proposal_id"] if entry else None,
            "service": entry["service"] if entry else None,
            "status": review["status"]
            if review
            else "preparation_interrupted"
            if entry
            else "not_prepared",
            "review_digest": review["review_digest"] if review else None,
            "source_import_id": entry["import_id"] if entry else None,
            "source_version": entry["version"] if entry else None,
            "source_preview_digest": entry["preview_digest"] if entry else None,
            "verified_result": json.loads(entry["result"])
            if entry and entry["result"]
            else None,
            "approval": "separate_terminal_review_required",
            "source_current": bool(
                entry
                and current_op is not None
                and json.loads(entry["operation"])["source"] == current_op
            ),
            "execution": self._service(entry["service"]).status(
                campaign_id, entry["proposal_id"]
            )
            if review
            else None,
        }

    def _overview(self, row: dict) -> dict:
        """Describe independent readiness; never approve or execute a batch."""
        ops = {
            op["key"]: op
            for op in row["state"].get("preview", [])
            if op["kind"] == "operation"
        }
        states = {key: self.inspect(row["campaign_id"], row["id"], key) for key in ops}
        sources = {
            op["key"]: op for op in row["state"].get("plan", {}).get("operations", [])
        }
        output = []
        for key, op in ops.items():
            state = states[key]
            execution = state["execution"] or {}
            uncertain = "uncertain" in execution.get("progress", {}) or execution.get(
                "outcome"
            ) in ("intent", "uncertain")
            blocked_by = [
                dependency
                for dependency in op["dependencies"]
                if not (
                    states[dependency]["status"] == "complete"
                    and states[dependency]["verified_result"]
                    and states[dependency]["source_import_id"] == row["id"]
                    and states[dependency]["source_preview_digest"]
                    == digest(row["state"]["preview"])
                )
            ]
            source_blockers = set(op["blockers"]) - self._dependency_blockers(
                op, sources[key]
            )
            phase = (
                "uncertain"
                if uncertain
                else "source_changed"
                if state["proposal_id"] and not state["source_current"]
                else "verified"
                if state["verified_result"] and state["status"] == "complete"
                else "needs_result_verification"
                if state["status"] == "complete"
                else "already_represented"
                if op["status"] == "already_represented"
                else "deferred"
                if op["status"] == "deferred"
                else "blocked"
                if blocked_by or source_blockers
                else state["status"]
                if state["status"] != "not_prepared"
                else "ready_for_preparation"
            )
            output.append({**state, "execution_state": phase, "blocked_by": blocked_by})
        return {
            "import_id": row["id"],
            "complete": True,
            "operations": output,
            "execution_complete": bool(output)
            and all(
                op["execution_state"] in ("verified", "already_represented")
                for op in output
            ),
            "ready_operations": [
                op["operation_key"]
                for op in output
                if op["execution_state"] == "ready_for_preparation"
            ],
            "policy": (
                "Pause uncertain operations and their dependents; inspect shared "
                "access/quota failures before further requests. Approval remains "
                "per operation."
            ),
        }

    async def refresh(
        self, campaign_id: int, import_id: str, operation_key: str
    ) -> dict:
        """Record current GET-verified results only after executor completion."""
        row = self.notes._load(campaign_id, import_id)
        op = self._operation(row, operation_key)
        entry = self._entry(row, op)
        if not entry:
            raise ValueError("Operation has no supervised preparation")
        if json.loads(entry["operation"])["source"] != op:
            raise ValueError("Source operation changed; inspect its historical handoff")
        review = self._review(campaign_id, entry)
        if not review or review["status"] != "complete":
            raise ValueError(
                "Executor is not complete; inspect status and reconcile uncertainty"
            )
        service = self._service(entry["service"])
        if entry["service"] == "creation":
            if (
                not review["steps"]
                or any(v != "confirmed" for v in review["steps"].values())
                or "uncertain" in review["progress"]
            ):
                raise ValueError("Creation has unconfirmed or uncertain steps")
            draft = service.store.draft(entry["proposal_id"])
            kind = creation_kind(draft)
            progress = review["progress"]
            result = progress[kind]
            refs = await service._references(draft)
            if refs != review["references"]:
                raise ValueError(
                    "note_evidence_stale: prepared reference identity changed"
                )
            module_ids = {
                uid: refs["entities"][str(uid)]["child_id"]
                for uid, expected in referenced_entities(draft)
                if expected is not None
            }
            if kind == "post":
                await service._verify(draft, progress, module_ids)
            else:
                # Later posts/attachments do not invalidate the created core's ID.
                core = await self.notes.client.get_entity(
                    campaign_id, result["entity_id"]
                )
                if (
                    core["entity"]["entity_type"] != kind
                    or core["entity"]["child_id"] != result["id"]
                ):
                    raise ValueError("Created core identity changed")
                _, expected = resolve_creation_request(
                    draft, kind, module_ids=module_ids
                )
                if not matches_creation_fields(expected, creation_details(draft, core)):
                    raise ValueError("note_evidence_stale: created core fields changed")
            verified = {
                "entity_id": result["entity_id"],
                "entity_type": kind,
                "record_id" if kind == "post" else "child_id": result["id"],
            }
        else:
            if review["outcome"] != "confirmed":
                raise ValueError("Edit outcome is not confirmed")
            draft = EditDraft.model_validate(review["plan"]["draft"])
            await service._verify(draft, review["plan"])
            verified = {
                "entity_id": draft.entity_id,
                "entity_type": draft.kind,
                "record_id": draft.record_id,
            }
        verified.update(
            proposal_id=entry["proposal_id"],
            review_digest=review["review_digest"],
            verified_at=time.time(),
        )
        with self.notes.store.db:
            self.notes.store.db.execute(
                "UPDATE note_handoffs SET result=? WHERE proposal_id=?",
                (json.dumps(verified), entry["proposal_id"]),
            )
        return self.inspect(campaign_id, import_id, operation_key)

    async def _mentions(self, row: dict, op: dict) -> None:
        state = row["state"]
        plan = {o["key"]: o for o in state["plan"]["operations"]}
        refs = [plan[k].get("target") for k in op["source_operations"]]
        refs += [r["target"] for r in op["references"]]
        keys = {r["key"] for r in refs if r and r["kind"] == "mention"}
        for claim in state["analysis"]["claims"]:
            if claim["key"] in op["claim_keys"]:
                keys.update(v for v in (claim["subject"], claim["attribution"]) if v)
        lookups = [
            state["lookups"][k]
            for k in sorted(keys)
            if state["lookups"][k]["status"] == "resolved"
        ]
        if len(lookups) > self.notes.reads_per_call:
            raise ValueError("Handoff identity budget exceeded; split the operation")
        for lookup in lookups:
            current = await self.notes.client.get_entity(
                row["campaign_id"], lookup["candidate"]["entity_id"]
            )
            if current != lookup["core"]:
                raise ValueError(
                    "note_evidence_stale: source canon changed; refresh analysis"
                )

    async def prepare(
        self,
        campaign_id: int,
        import_id: str,
        expected_version: int,
        preview_digest: str,
        operation_key: str,
    ) -> dict:
        """Prepare one selected operation, without approval or any Kanka write."""
        row = self.notes._load(campaign_id, import_id)
        if type(expected_version) is not int or row["version"] != expected_version:
            raise ValueError("note_revision_conflict: inspect the current preview")
        if (
            "preview" not in row["state"]
            or digest(row["state"]["preview"]) != preview_digest
        ):
            raise ValueError("Reviewed preview digest does not match")
        if row["state"].get("epoch") != self.notes.epoch:
            raise ValueError("note_evidence_stale: refresh analysis after restart")
        op = self._operation(row, operation_key)
        source = {o["key"]: o for o in row["state"]["plan"]["operations"]}[op["key"]]
        allowed = self._dependency_blockers(op, source)
        if (
            op["action"] == "deferred"
            or op["status"] == "already_represented"
            or set(op["blockers"]) - allowed
        ):
            raise ValueError("Operation is blocked, deferred or already represented")
        service_kind = (
            "creation"
            if op["action"] in ("create_entity", "create_post")
            else "editing"
        )
        service = self._service(service_kind)
        previous = self._entry(row, op)
        if previous:
            if json.loads(previous["operation"])["source"] != op:
                raise ValueError(
                    "Logical operation has a different handoff; review it explicitly"
                )
            review = self._review(campaign_id, previous)
            if review:
                return self.inspect(campaign_id, import_id, operation_key)
        await self._mentions(row, op)
        bindings = {}
        for key in op["dependencies"]:
            result = (await self.refresh(campaign_id, import_id, key))[
                "verified_result"
            ]
            if result["entity_type"] == "post" or "child_id" not in result:
                raise ValueError("A dependency must be a verified created core")
            bindings[key] = result["entity_id"]
        payload = deepcopy(op["payload"])
        for field in {r["field"] for r in op["references"]}:
            values = []
            for ref in op["references"]:
                if ref["field"] == field:
                    target = ref["target"]
                    values.append(
                        bindings[target["key"]]
                        if target["kind"] == "proposal"
                        else row["state"]["lookups"][target["key"]]["candidate"][
                            "entity_id"
                        ]
                    )
            payload[field] = values if field.endswith("_ids") else values[0]
        target_id = op["target_entity_id"]
        if (source.get("target") or {}).get("kind") == "proposal":
            target_id = bindings[source["target"]["key"]]
        if service_kind == "creation":
            if op["action"] == "create_post":
                draft = EntityPostDraft.model_validate_json(
                    json.dumps(
                        {**payload, "campaign_id": campaign_id, "entity_id": target_id}
                    )
                )
            else:
                draft = DRAFT_MODELS[op["entity_type"]].model_validate_json(
                    json.dumps({**payload, "campaign_id": campaign_id})
                )
            expected_before = None
            expected_owner = None
        else:
            changes = {key: payload[key] for key in op["diff"]}
            draft = EditDraft(
                campaign_id=campaign_id,
                entity_id=target_id,
                kind=op["entity_type"],
                record_id=op["record_id"],
                changes=changes,
            )
            expected_before = {
                REFERENCE_FIELDS.get(key, (key, None))[0]: value["before"]
                for key, value in op["diff"].items()
            }
            lookup = row["state"]["lookups"][source["target"]["key"]]
            expected_owner = {
                key: lookup["core"]["details"][key]
                for key in ("id", "entity_id", "is_private", "updated_at")
                if key in lookup["core"]["details"]
            }
            if op["action"] == "edit_entity":
                baseline = lookup["core"]["details"]
            else:
                section = {
                    "post": "posts",
                    "inventory": "inventory",
                    "relation": "relations",
                }[op["entity_type"]]
                baseline = next(
                    item
                    for item in row["state"]["collections"][f"{target_id}-{section}"][
                        "data"
                    ]
                    if item["id"] == op["record_id"]
                )
            expected_before.update(
                {
                    key: baseline[key]
                    for key in (
                        "id",
                        "entity_id",
                        "is_private",
                        "visibility_id",
                        "visibility",
                        "updated_at",
                    )
                    if key in baseline
                }
            )
        intent = {"source": op, "draft": draft.model_dump(mode="json")}
        if "publication" in op:
            sources = deepcopy(op["publication"]["public_sources"])
            for uid in sorted(set(bindings.values())):
                source_ref = {"entity_id": uid, "section": "core"}
                projection = await public_source(
                    self.notes.client, campaign_id, source_ref
                )
                sources.append({**source_ref, "fingerprint": digest(projection)})
            if len(sources) > self.notes.reads_per_call:
                raise ValueError("Public source budget exceeded; split the operation")
            intent["publication_sources"] = sources
        if service_kind == "creation":
            service.scope.check(draft)
        else:
            service._scope(draft)
        proposal_id = ("edit_note_" if service_kind == "editing" else "note_") + digest(
            [row["document_id"], op["logical_key"], intent]
        )
        db = self.notes.store.db
        with db:
            db.execute("BEGIN IMMEDIATE")
            if (
                self.notes.store.load(campaign_id, import_id)["version"]
                != expected_version
            ):
                raise ValueError(
                    "note_revision_conflict: source changed during preparation"
                )
            previous = self._entry(row, op)
            if previous and (
                previous["proposal_id"] != proposal_id
                or json.loads(previous["operation"]) != intent
            ):
                raise ValueError(
                    "Logical operation is already bound to another handoff"
                )
            db.execute(
                "INSERT OR IGNORE INTO note_handoffs VALUES (?,?,?,?,?,?,?,?,NULL)",
                (
                    row["document_id"],
                    op["logical_key"],
                    import_id,
                    expected_version,
                    preview_digest,
                    json.dumps(intent, ensure_ascii=False),
                    service_kind,
                    proposal_id,
                ),
            )
        provenance = self._provenance(self._entry(row, op))
        if service_kind == "creation":
            await service.prepare(draft, proposal_id=proposal_id, provenance=provenance)
        else:
            await service.prepare(
                draft,
                proposal_id=proposal_id,
                expected_before=expected_before,
                expected_owner=expected_owner,
                provenance=provenance,
            )
        return self.inspect(campaign_id, import_id, operation_key)
