"""Private, explicitly configured creation progress; no network or approvals.

SQLite commits intent before an executor may send a request. Restored intent is
uncertain, never permission to retry. Reconciliation is a trusted-host action;
the host must stop other workers before reviewing/resolving uncertain operations.
"""

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from typing import Any

from loremaster.creation import ApprovedCreation, resolve_creation_request
from loremaster.verification import matches_creation_fields


def fingerprint(path: str, body: dict[str, Any]) -> str:
    """Bind POST's resolved path and exact JSON body, including Unicode lore."""
    return hashlib.sha256(
        json.dumps(["POST", path, body], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class CreationJournal:
    """Persist scoped operation intent/results, never bearer or approval tokens.

    The caller explicitly selects a private local file outside the public repo.
    Its parent must exist. The file contains request bodies (potentially private
    lore); new files have mode 0600, and insecure existing files are rejected.
    Close the journal on shutdown. It is not a Kanka transaction or approval UI.
    """

    def __init__(self, path: Path) -> None:
        path = Path(path)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
                raise PermissionError(
                    "Journal must be a private regular file"
                ) from None
        else:
            os.close(descriptor)
        self._db = sqlite3.connect(path, timeout=1)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY, campaign_id INTEGER NOT NULL,
                digest TEXT NOT NULL, refs TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                execution_id TEXT NOT NULL REFERENCES executions(id),
                key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                path TEXT NOT NULL, body TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('intent', 'confirmed', 'rejected', 'uncertain')
                ),
                result TEXT, approval_id TEXT NOT NULL, reviewer TEXT NOT NULL,
                reconciled_by TEXT,
                PRIMARY KEY (execution_id, key)
            );
        """)

    def close(self) -> None:
        """Close the journal; committed intent/progress survives shutdown."""
        self._db.close()

    @staticmethod
    def _id(execution_id: str) -> None:
        if not isinstance(execution_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", execution_id
        ):
            raise ValueError("execution_id must be 1–128 ASCII letters/digits/_/-")

    def bind(
        self, execution_id: str, approved: ApprovedCreation, refs: dict[int, int]
    ) -> dict[str, Any]:
        """Bind a stable execution ID to content, campaign and resolved item IDs.

        A newly issued approval may resume the same execution, but changed
        content or references cannot reuse its ID. Identical drafts with distinct
        execution IDs remain distinct requests, subject to duplicate-name checks.
        """
        self._id(execution_id)
        if any(
            type(value) is not int or value <= 0
            for pair in refs.items()
            for value in pair
        ):
            raise ValueError("Resolved item references must be positive integer IDs")
        values = (
            execution_id,
            approved.draft.campaign_id,
            approved.content_digest,
            json.dumps(refs, sort_keys=True),
        )
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO executions VALUES (?, ?, ?, ?)", values
            )
            row = self._db.execute(
                "SELECT * FROM executions WHERE id = ?", (execution_id,)
            ).fetchone()
            if tuple(row) != values:
                raise ValueError(
                    "Execution ID is bound to different content or references"
                )
        return self.progress(execution_id)

    def progress(self, execution_id: str) -> dict[str, Any]:
        """Restore confirmed IDs; intent/uncertainty always blocks further sends."""
        self._id(execution_id)
        result = {}
        for row in self._db.execute(
            "SELECT * FROM steps WHERE execution_id = ? ORDER BY rowid", (execution_id,)
        ):
            if row["state"] == "confirmed":
                result[row["key"]] = json.loads(row["result"])
            elif row["state"] in ("intent", "uncertain"):
                result["uncertain"] = row["key"]
        return result

    def states(self, execution_id: str) -> dict[str, str]:
        """Read each step's latest outcome without exposing stored request bodies."""
        self._id(execution_id)
        return dict(
            self._db.execute(
                "SELECT key, state FROM steps WHERE execution_id = ? ORDER BY rowid",
                (execution_id,),
            ).fetchall()
        )

    def begin(
        self,
        execution_id: str,
        approved: ApprovedCreation,
        key: str,
        path: str,
        body: dict[str, Any],
    ) -> None:
        """Commit intent once; known rejections alone permit a fresh approved try."""
        self._id(execution_id)
        if not path.startswith(f"campaigns/{approved.draft.campaign_id}/"):
            raise ValueError("Operation is outside the approved campaign")
        digest = fingerprint(path, body)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            execution = self._db.execute(
                "SELECT * FROM executions WHERE id = ?", (execution_id,)
            ).fetchone()
            if execution is None or (
                execution["campaign_id"] != approved.draft.campaign_id
                or execution["digest"] != approved.content_digest
            ):
                raise ValueError("Execution is not bound to this approval")
            progress = self.progress(execution_id)
            if "uncertain" in progress:
                raise RuntimeError("Uncertain intent requires reconciliation")
            expected_path, expected_body = resolve_creation_request(
                approved.draft,
                key,
                entity_id=progress.get("character", {}).get("entity_id"),
                item_ids={
                    int(key): value
                    for key, value in json.loads(execution["refs"]).items()
                },
            )
            if digest != fingerprint(expected_path, expected_body):
                raise ValueError("Resolved request differs from approved content")
            row = self._db.execute(
                "SELECT * FROM steps WHERE execution_id = ? AND key = ?",
                (execution_id, key),
            ).fetchone()
            if row is not None:
                if (
                    row["state"] != "rejected"
                    or row["fingerprint"] != digest
                    or row["approval_id"] == approved.proposal_id
                ):
                    raise RuntimeError(
                        "Step cannot be resent without reconciliation or fresh approval"
                    )
                self._db.execute(
                    "DELETE FROM steps WHERE execution_id = ? AND key = ?",
                    (execution_id, key),
                )
            self._db.execute(
                "INSERT INTO steps VALUES (?, ?, ?, ?, ?, 'intent', NULL, ?, ?, NULL)",
                (
                    execution_id,
                    key,
                    digest,
                    path,
                    json.dumps(body, ensure_ascii=False),
                    approved.proposal_id,
                    approved.approved_by,
                ),
            )

    def finish(
        self,
        execution_id: str,
        key: str,
        state: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Commit validated response IDs or a sanitized failure classification."""
        if state not in ("confirmed", "rejected", "uncertain"):
            raise ValueError("Invalid operation outcome")
        identity = self._identity(result) if state == "confirmed" else None
        with self._db:
            cursor = self._db.execute(
                "UPDATE steps SET state = ?, result = ? "
                "WHERE execution_id = ? AND key = ? AND state = 'intent'",
                (state, json.dumps(identity) if identity else None, execution_id, key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Operation intent changed or is missing")

    @staticmethod
    def _identity(result: dict[str, Any] | None) -> dict[str, int]:
        if (
            not isinstance(result, dict)
            or type(result.get("id")) is not int
            or result["id"] <= 0
        ):
            raise ValueError("Result must contain a positive record ID")
        identity = {
            key: result[key] for key in ("id", "entity_id", "owner_id") if key in result
        }
        if any(type(value) is not int or value <= 0 for value in identity.values()):
            raise ValueError("Invalid result identity")
        return identity

    def pending(self, execution_id: str) -> dict[str, Any]:
        """Return one uncertain intent for explicit, scoped read-back inspection."""
        self._id(execution_id)
        rows = self._db.execute(
            "SELECT s.*, e.campaign_id FROM steps s JOIN executions e "
            "ON s.execution_id = e.id WHERE s.execution_id = ? "
            "AND s.state IN ('intent', 'uncertain')",
            (execution_id,),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError("Execution must have exactly one uncertain operation")
        row = dict(rows[0])
        row["body"] = json.loads(row["body"])
        if fingerprint(row["path"], row["body"]) != row["fingerprint"]:
            raise ValueError("Stored request fingerprint mismatch")
        return row

    def confirm_reconciled(
        self,
        execution_id: str,
        fingerprint: str,
        result: dict[str, Any],
        *,
        reviewed_by: str,
    ) -> None:
        """Trusted host records reviewed matching evidence; this is not approval.

        The caller must stop other workers and verify the scoped API record
        against pending() first. Missing/ambiguous evidence must stay unresolved.
        A new approval is still required before resuming execution.
        """
        if not isinstance(reviewed_by, str) or not reviewed_by.strip():
            raise ValueError("Reconciliation requires an identified reviewer")
        row = self.pending(execution_id)
        if row["fingerprint"] != fingerprint:
            raise ValueError("Reviewed intent does not match pending operation")
        identity = self._identity(result)
        if not matches_creation_fields(row["body"], result):
            raise ValueError("Reconciled record differs from the pending request")
        if row["key"] == "character" and "entity_id" not in identity:
            raise ValueError("Reconciled character lacks its universal entity ID")
        with self._db:
            cursor = self._db.execute(
                "UPDATE steps SET state = 'confirmed', result = ?, reconciled_by = ? "
                "WHERE execution_id = ? AND key = ? AND state = ? AND fingerprint = ?",
                (
                    json.dumps(identity),
                    reviewed_by.strip(),
                    execution_id,
                    row["key"],
                    row["state"],
                    fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Pending operation changed during reconciliation")
