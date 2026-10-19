"""Local OS-authorized review records shared by the CLI and opt-in MCP executor.

The SQLite file is a host authority boundary. Anyone able to modify it is a
trusted administrator. Never expose approve/revoke through an MCP tool.
"""

import hashlib
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from loremaster.creation import (
    MAX_PENDING_PROPOSALS,
    ApprovedCreation,
    CharacterDraft,
    preview_creation,
)
from loremaster.progress import CreationJournal


class LocalApprovals:
    """Persist immutable proposals and expiring, single-use host approvals.

    No API token or approval bearer token is stored. OS access to the private
    database authorizes the separate local approval command. This is not remote
    authentication or protection against a compromised local administrator.
    """

    def __init__(self, path: Path) -> None:
        self.journal = CreationJournal(path)
        self._db = self.journal._db
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, approval_id TEXT, reviewer TEXT,
                deadline REAL
            )
        """)
        self._db.commit()

    def close(self) -> None:
        """Release the shared approval/progress connection."""
        self.journal.close()

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()

    def prepare(
        self, draft: CharacterDraft, references: dict[str, Any]
    ) -> dict[str, Any]:
        """Save an unapproved, immutable draft and its reviewed reference identities."""
        payload = {"draft": draft.model_dump(mode="json"), "references": references}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 128 * 1024:
            raise ValueError("Creation proposal exceeds the 128 KiB review budget")
        proposal_id = uuid.uuid4().hex
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            count = self._db.execute(
                "SELECT COUNT(*) FROM proposals WHERE state = 'pending'"
            ).fetchone()[0]
            if count >= MAX_PENDING_PROPOSALS:
                raise ValueError("Too many unreviewed creation proposals")
            self._db.execute(
                "INSERT INTO proposals VALUES (?, ?, ?, 'pending', NULL, NULL, NULL)",
                (
                    proposal_id,
                    json.dumps(payload, ensure_ascii=False),
                    self._digest(payload),
                ),
            )
        return self.review(proposal_id)

    def _load(self, proposal_id: str) -> tuple[Any, CharacterDraft, dict[str, Any]]:
        if (
            not isinstance(proposal_id, str)
            or not proposal_id
            or len(proposal_id) > 128
        ):
            raise PermissionError("Invalid proposal reference")
        row = self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise PermissionError("Proposal is unavailable")
        try:
            payload = json.loads(row["payload"])
            draft = CharacterDraft.model_validate_json(json.dumps(payload["draft"]))
            if self._digest(payload) != row["digest"] or not isinstance(
                payload["references"], dict
            ):
                raise ValueError("Changed proposal")
        except (ValueError, KeyError, TypeError):
            raise PermissionError(
                "Stored proposal failed integrity validation"
            ) from None
        return row, draft, payload["references"]

    def review(self, proposal_id: str) -> dict[str, Any]:
        """Return exact review content and progress; never include a capability."""
        row, draft, references = self._load(proposal_id)
        return {
            "proposal_id": proposal_id,
            "status": row["state"],
            "review_digest": row["digest"],
            "preview": preview_creation(draft),
            "references": references,
            "approval_expires_at": row["deadline"],
            "reviewed_by": row["reviewer"],
            "progress": self.journal.progress(proposal_id),
            "steps": self.journal.states(proposal_id),
            "inventory_visibility": "Kanka default; per-entry privacy is not configured"
            if draft.inventory
            else None,
        }

    def draft(self, proposal_id: str) -> CharacterDraft:
        """Return the integrity-checked immutable draft for backend scope checks."""
        return self._load(proposal_id)[1]

    def approve(self, proposal_id: str, reviewed_digest: str, reviewer: str) -> None:
        """Trusted local command only: authorize the reviewed content for five minutes.

        Reapproval is an explicit new review, including for a partially completed
        request. It never erases journal uncertainty or permits blind retries.
        """
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("An identified local reviewer is required")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row, _draft, _refs = self._load(proposal_id)
            if row["state"] in ("revoked", "complete"):
                raise PermissionError("Proposal is revoked or already complete")
            if "uncertain" in self.journal.progress(proposal_id):
                raise PermissionError(
                    "Reconcile uncertain intent before approving execution"
                )
            if (
                not isinstance(reviewed_digest, str)
                or not reviewed_digest.isascii()
                or not secrets.compare_digest(reviewed_digest, row["digest"])
            ):
                raise PermissionError("Reviewed content does not match the proposal")
            self._db.execute(
                "UPDATE proposals SET state = 'approved', approval_id = ?, "
                "reviewer = ?, "
                "deadline = ? WHERE id = ?",
                (uuid.uuid4().hex, reviewer.strip(), time.time() + 300, proposal_id),
            )

    def revoke(self, proposal_id: str) -> None:
        """Trusted local command: stop claims and subsequent sends."""
        self._load(proposal_id)
        with self._db:
            self._db.execute(
                "UPDATE proposals SET state = 'revoked' WHERE id = ?", (proposal_id,)
            )

    def claim(self, proposal_id: str) -> ApprovedCreation:
        """Atomically consume one host approval, independently of public checksums."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row, draft, _refs = self._load(proposal_id)
            if row["state"] != "approved" or row["deadline"] <= time.time():
                raise PermissionError("A current, unused local approval is required")
            self._db.execute(
                "UPDATE proposals SET state = 'claimed' WHERE id = ?", (proposal_id,)
            )
        return ApprovedCreation(
            row["approval_id"],
            preview_creation(draft)["content_digest"],
            row["reviewer"],
            draft,
        )

    def check(self, proposal_id: str, approved: ApprovedCreation) -> None:
        """Recheck expiry, revocation and exact content immediately before each send."""
        row, draft, _refs = self._load(proposal_id)
        if (
            row["state"] != "claimed"
            or row["approval_id"] != approved.proposal_id
            or row["deadline"] <= time.time()
            or draft != approved.draft
            or row["reviewer"] != approved.approved_by
            or preview_creation(draft)["content_digest"] != approved.content_digest
        ):
            raise PermissionError("Creation approval expired, changed or was revoked")

    def complete(self, proposal_id: str, approved: ApprovedCreation) -> None:
        """Record verified completion without making the approval reusable."""
        with self._db:
            self._db.execute(
                "UPDATE proposals SET state = 'complete' WHERE id = ? "
                "AND approval_id = ? AND state = 'claimed'",
                (proposal_id, approved.proposal_id),
            )
