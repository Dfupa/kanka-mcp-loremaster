"""Additive private edit storage; creation rows and POST fingerprints stay intact."""

import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from loremaster.approvals import LocalApprovals
from loremaster.creation import MAX_PENDING_PROPOSALS
from loremaster.edit_models import EditDraft, build_edit_plan
from loremaster.progress import CreationJournal


class EditStore:
    """One PATCH intent per immutable proposal, with local single-use approval.

    The same-OS private file is the authority boundary, not remote authentication.
    Intent/uncertainty blocks resend even after restarting or issuing approval.
    """

    def __init__(self, path: Path) -> None:
        self.journal = CreationJournal(path)
        self._db = self.journal._db
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS edit_proposals (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, outcome TEXT NOT NULL,
                approval_id TEXT, reviewer TEXT, deadline REAL,
                reconciled_by TEXT
            )
        """)
        self._db.commit()

    def close(self) -> None:
        """Release this connection without modifying creation records."""
        self.journal.close()

    def _uncertain_owner(self, draft: EditDraft, exclude: str | None = None) -> None:
        """A new proposal cannot bypass an unresolved write to this character."""
        rows = self._db.execute(
            "SELECT id FROM edit_proposals WHERE outcome IN ('intent', 'uncertain')"
        ).fetchall()
        for row in rows:
            if row["id"] == exclude:
                continue
            _record, plan = self._load(row["id"])
            other = plan["draft"]
            if (other["campaign_id"], other["entity_id"]) == (
                draft.campaign_id,
                draft.entity_id,
            ):
                raise PermissionError("Reconcile the character's uncertain edit first")

    def prepare(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Store an unapproved exact baseline, references and requested diff."""
        draft = EditDraft.model_validate(plan["draft"])
        expected = build_edit_plan(draft, plan["before"], plan["references"])
        if plan != expected:
            raise ValueError("Invalid derived edit plan")
        payload = json.dumps(plan, ensure_ascii=False, allow_nan=False)
        if len(payload.encode()) > 128 * 1024:
            raise ValueError("Edit proposal exceeds the 128 KiB review budget")
        proposal_id = "edit_" + uuid.uuid4().hex
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._uncertain_owner(draft)
            count = self._db.execute(
                "SELECT COUNT(*) FROM edit_proposals WHERE state = 'pending'"
            ).fetchone()[0]
            if count >= MAX_PENDING_PROPOSALS:
                raise ValueError("Too many unreviewed edit proposals")
            self._db.execute(
                "INSERT INTO edit_proposals VALUES "
                "(?, ?, ?, 'pending', 'not_sent', NULL, NULL, NULL, NULL)",
                (proposal_id, payload, LocalApprovals._digest(plan)),
            )
        return self.review(proposal_id)

    def _load(self, proposal_id: str) -> tuple[Any, dict[str, Any]]:
        CreationJournal._id(proposal_id)
        row = self._db.execute(
            "SELECT * FROM edit_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise PermissionError("Edit proposal is unavailable")
        try:
            plan = json.loads(row["payload"])
            draft = EditDraft.model_validate(plan["draft"])
            if row["digest"] != LocalApprovals._digest(plan) or plan != build_edit_plan(
                draft, plan["before"], plan["references"]
            ):
                raise ValueError("Changed edit")
        except (ValueError, KeyError, TypeError):
            raise PermissionError("Stored edit failed integrity validation") from None
        return row, plan

    def review(self, proposal_id: str) -> dict[str, Any]:
        """Read exact private review content and durable progress, never a grant."""
        row, plan = self._load(proposal_id)
        return {
            "proposal_id": proposal_id,
            "status": row["state"],
            "outcome": row["outcome"],
            "review_digest": row["digest"],
            "approval_expires_at": row["deadline"],
            "reviewed_by": row["reviewer"],
            "reconciled_by": row["reconciled_by"],
            "plan": plan,
        }

    def approve(self, proposal_id: str, digest: str, reviewer: str) -> None:
        """Local host only: approve the unchanged proposal for five minutes."""
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("An identified local reviewer is required")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row, _plan = self._load(proposal_id)
            if row["state"] in ("revoked", "stale", "complete"):
                raise PermissionError("Edit is revoked, stale or complete")
            if row["outcome"] in ("intent", "uncertain"):
                raise PermissionError("Reconcile uncertain edit intent first")
            if (
                not isinstance(digest, str)
                or not digest.isascii()
                or not secrets.compare_digest(digest, row["digest"])
            ):
                raise PermissionError("Reviewed edit content does not match")
            self._db.execute(
                "UPDATE edit_proposals SET state = 'approved', approval_id = ?, "
                "reviewer = ?, deadline = ? WHERE id = ?",
                (uuid.uuid4().hex, reviewer.strip(), time.time() + 300, proposal_id),
            )

    def revoke(self, proposal_id: str) -> None:
        """Prevent future claims/sends; an in-flight request cannot be retracted."""
        self._load(proposal_id)
        with self._db:
            self._db.execute(
                "UPDATE edit_proposals SET state = 'revoked' WHERE id = ?",
                (proposal_id,),
            )

    def claim(self, proposal_id: str) -> str:
        """Atomically consume the approval; return a process-internal grant ID."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row, _plan = self._load(proposal_id)
            if (
                row["state"] != "approved"
                or row["deadline"] <= time.time()
                or row["outcome"] in ("intent", "uncertain")
            ):
                raise PermissionError(
                    "A current, unused local edit approval is required"
                )
            self._db.execute(
                "UPDATE edit_proposals SET state = 'claimed' WHERE id = ?",
                (proposal_id,),
            )
            return row["approval_id"]

    def check(self, proposal_id: str, grant: str) -> None:
        """Recheck content integrity, expiry and revocation at the send boundary."""
        row, _plan = self._load(proposal_id)
        if (
            row["state"] != "claimed"
            or row["approval_id"] != grant
            or row["deadline"] <= time.time()
        ):
            raise PermissionError("Edit approval expired or was revoked")

    def begin(self, proposal_id: str, grant: str) -> None:
        """Commit method/path/body/baseline-bound intent before the single PATCH."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self.check(proposal_id, grant)
            row, _plan = self._load(proposal_id)
            if row["outcome"] not in ("not_sent", "rejected"):
                raise PermissionError("Edit intent cannot be resent")
            self._uncertain_owner(EditDraft.model_validate(_plan["draft"]), proposal_id)
            self._db.execute(
                "UPDATE edit_proposals SET outcome = 'intent' WHERE id = ?",
                (proposal_id,),
            )

    def finish(self, proposal_id: str, outcome: str) -> None:
        """Record a response classification; uncertainty cannot authorize a retry."""
        if outcome not in ("rejected", "uncertain", "confirmed"):
            raise ValueError("Invalid edit outcome")
        with self._db:
            cursor = self._db.execute(
                "UPDATE edit_proposals SET outcome = ? "
                "WHERE id = ? AND outcome = 'intent'",
                (outcome, proposal_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Edit intent is missing or changed")

    def stale(self, proposal_id: str, grant: str) -> None:
        """Invalidate an approved baseline; a new proposal/diff is required."""
        with self._db:
            self.check(proposal_id, grant)
            self._db.execute(
                "UPDATE edit_proposals SET state = 'stale' WHERE id = ?", (proposal_id,)
            )

    def complete(self, proposal_id: str, grant: str) -> None:
        """Record independently verified completion, without reusing approval."""
        with self._db:
            self.check(proposal_id, grant)
            cursor = self._db.execute(
                "UPDATE edit_proposals SET state = 'complete' "
                "WHERE id = ? AND outcome = 'confirmed'",
                (proposal_id,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Edit result is not confirmed")

    def confirm_reconciled(self, proposal_id: str, digest: str, reviewer: str) -> None:
        """Trusted host only, after stopped-worker/origin review of matching reads."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row, _plan = self._load(proposal_id)
            if (
                row["outcome"] not in ("intent", "uncertain")
                or row["digest"] != digest
                or not isinstance(reviewer, str)
                or not reviewer.strip()
            ):
                raise PermissionError(
                    "Edit reconciliation does not match pending intent"
                )
            self._db.execute(
                "UPDATE edit_proposals SET outcome = 'confirmed', "
                "reconciled_by = ? WHERE id = ?",
                (reviewer.strip(), proposal_id),
            )
