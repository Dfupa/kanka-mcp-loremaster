"""Explicit private SQLite storage for source revisions, never write approvals."""

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from pathlib import Path
from typing import Any

from loremaster.note_models import ImportContext
from loremaster.note_text import decode_source, source_blocks


def digest(value: Any) -> str:
    """Hash a canonical JSON value without normalizing its source strings."""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


class NoteStore:
    """Store immutable sources and CAS snapshots in an explicitly selected file."""

    def __init__(self, path: Path):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError("Notes database must be an absolute private file")
        if any((parent / ".git").exists() for parent in path.resolve().parents):
            raise ValueError("Notes database must stay outside the repository")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            mode = os.fstat(descriptor).st_mode
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
                raise PermissionError("Notes database must be a private regular file")
        finally:
            os.close(descriptor)
        self.db = sqlite3.connect(path, timeout=1)
        self.db.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if tables - {"documents", "imports", "snapshots", "note_handoffs"}:
            self.db.close()
            raise PermissionError(
                "Use a separate notes database, not an existing application store"
            )
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, campaign_id INTEGER NOT NULL, context TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS imports (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                source_hash TEXT NOT NULL, label TEXT NOT NULL, method TEXT NOT NULL,
                encoding TEXT, raw BLOB NOT NULL, blocks TEXT NOT NULL,
                version INTEGER NOT NULL,
                extraction TEXT NOT NULL DEFAULT '{}',
                variant_hash TEXT NOT NULL DEFAULT '',
                UNIQUE(document_id, source_hash, variant_hash)
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                import_id TEXT NOT NULL REFERENCES imports(id),
                version INTEGER NOT NULL, base_version INTEGER NOT NULL,
                request_hash TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(import_id, version),
                UNIQUE(import_id, base_version, request_hash)
            );
            CREATE TABLE IF NOT EXISTS note_handoffs (
                document_id TEXT NOT NULL REFERENCES documents(id),
                logical_key TEXT NOT NULL,
                import_id TEXT NOT NULL REFERENCES imports(id),
                version INTEGER NOT NULL, preview_digest TEXT NOT NULL,
                operation TEXT NOT NULL, service TEXT NOT NULL,
                proposal_id TEXT NOT NULL UNIQUE, result TEXT,
                PRIMARY KEY(document_id, logical_key)
            );
        """)
        self._migrate_imports()

    def _migrate_imports(self) -> None:
        """Add extraction variants without changing old source IDs or snapshots."""
        if "extraction" in {
            r[1] for r in self.db.execute("PRAGMA table_info(imports)")
        }:
            return
        self.db.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                if "extraction" in {
                    r[1] for r in self.db.execute("PRAGMA table_info(imports)")
                }:
                    return
                self.db.execute("""CREATE TABLE imports_v2 (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    source_hash TEXT NOT NULL, label TEXT NOT NULL,
                    method TEXT NOT NULL,
                    encoding TEXT, raw BLOB NOT NULL, blocks TEXT NOT NULL,
                    version INTEGER NOT NULL, extraction TEXT NOT NULL DEFAULT '{}',
                    variant_hash TEXT NOT NULL DEFAULT '',
                    UNIQUE(document_id, source_hash, variant_hash))""")
                self.db.execute("""INSERT INTO imports_v2
                    (id,document_id,source_hash,label,method,encoding,raw,blocks,version)
                    SELECT id,document_id,source_hash,label,method,
                    encoding,raw,blocks,version
                    FROM imports""")
                self.db.execute("DROP TABLE imports")
                self.db.execute("ALTER TABLE imports_v2 RENAME TO imports")
                if self.db.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("Notes database extraction migration failed")
        finally:
            self.db.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.db.close()

    def import_bytes(
        self,
        campaign_id: int,
        raw: bytes,
        context: ImportContext,
        label: str,
        *,
        encoding: str | None = None,
        document_id: str | None = None,
        method: str = "txt",
        extraction: dict | None = None,
    ) -> dict:
        """Import exact bytes; explicit lineage and repeated bytes reuse an import."""
        if type(campaign_id) is not int or campaign_id <= 0:
            raise ValueError("Campaign ID must be a positive integer")
        if (
            not label.strip()
            or len(label) > 255
            or any(c in label for c in ("/", "\\", "\x00"))
        ):
            raise ValueError("Use a filename label, not a filesystem path")
        if extraction is None:
            text = decode_source(raw, encoding)
            blocks = source_blocks(text)
            metadata = {}
        else:
            if method != "pdf" or not raw or len(raw) > 8 * 1024 * 1024:
                raise ValueError("Invalid PDF extraction source")
            blocks, metadata = extraction["blocks"], extraction["metadata"]
            if metadata["source_sha256"] != hashlib.sha256(raw).hexdigest():
                raise ValueError("Extraction does not match the original PDF")
        source_hash = hashlib.sha256(raw).hexdigest()
        variant_hash = digest(metadata) if metadata else ""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if document_id:
                doc = self.db.execute(
                    "SELECT * FROM documents WHERE id=?", (document_id,)
                ).fetchone()
                if doc is None or doc["campaign_id"] != campaign_id:
                    raise PermissionError("Document is unavailable in this campaign")
                if json.loads(doc["context"]) != context.model_dump():
                    raise ValueError("Document lineage context cannot change")
                previous = self.db.execute(
                    "SELECT id,encoding FROM imports "
                    "WHERE document_id=? AND source_hash=? AND variant_hash=?",
                    (document_id, source_hash, variant_hash),
                ).fetchone()
                if previous:
                    if previous["encoding"] != encoding:
                        raise ValueError(
                            "Repeated bytes must use the original encoding"
                        )
                    return self.load(campaign_id, previous["id"])
            else:
                document_id = uuid.uuid4().hex
                self.db.execute(
                    "INSERT INTO documents VALUES (?,?,?)",
                    (document_id, campaign_id, context.model_dump_json()),
                )
            import_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO imports "
                "(id,document_id,source_hash,label,method,encoding,raw,"
                "blocks,version,extraction,variant_hash) "
                "VALUES (?,?,?,?,?,?,?,?,0,?,?)",
                (
                    import_id,
                    document_id,
                    source_hash,
                    label,
                    method,
                    encoding,
                    raw,
                    json.dumps(blocks, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    variant_hash,
                ),
            )
            self.db.execute(
                "INSERT INTO snapshots VALUES (?,0,-1,'import',?)",
                (import_id, json.dumps({})),
            )
        return self.load(campaign_id, import_id)

    def import_pdf(
        self,
        campaign_id: int,
        raw: bytes,
        context: ImportContext,
        label: str,
        *,
        document_id: str | None = None,
        start_page: int = 1,
        page_count: int = 64,
    ) -> dict:
        """Register a local PDF range with exact original bytes and page provenance."""
        from loremaster.note_pdf import extract_pdf

        extracted = extract_pdf(raw, start_page=start_page, page_count=page_count)
        return self.import_bytes(
            campaign_id,
            raw,
            context,
            label,
            document_id=document_id,
            method="pdf",
            extraction=extracted,
        )

    def load(self, campaign_id: int, import_id: str) -> dict:
        """Load only a campaign-scoped import; raw bytes stay outside tool results."""
        row = self.db.execute(
            "SELECT i.*,d.campaign_id,d.context FROM imports i "
            "JOIN documents d ON d.id=i.document_id "
            "WHERE i.id=? AND d.campaign_id=?",
            (import_id, campaign_id),
        ).fetchone()
        if row is None:
            raise PermissionError("Import is unavailable in this campaign")
        snapshot = self.db.execute(
            "SELECT state FROM snapshots WHERE import_id=? AND version=?",
            (import_id, row["version"]),
        ).fetchone()
        return {
            **dict(row),
            "context": json.loads(row["context"]),
            "blocks": json.loads(row["blocks"]),
            "extraction": json.loads(row["extraction"]),
            "state": json.loads(snapshot["state"]),
        }

    def replay(self, import_id: str, version: int, request_hash: str) -> dict | None:
        row = self.db.execute(
            "SELECT version,state FROM snapshots WHERE import_id=? "
            "AND base_version=? AND request_hash=?",
            (import_id, version, request_hash),
        ).fetchone()
        return (
            {"version": row["version"], "state": json.loads(row["state"])}
            if row
            else None
        )

    def save(self, row: dict, expected: int, request_hash: str, state: dict) -> dict:
        """Atomically append a snapshot; stale submissions cannot overwrite work."""
        serialized = json.dumps(state, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode()) > 32 * 1024 * 1024:
            raise ValueError("Note snapshot exceeds 32 MiB; split the import")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            changed = self.db.execute(
                "UPDATE imports SET version=version+1 WHERE id=? AND version=?",
                (row["id"], expected),
            ).rowcount
            if not changed:
                raise ValueError("note_revision_conflict: inspect the latest version")
            self.db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?)",
                (
                    row["id"],
                    expected + 1,
                    expected,
                    request_hash,
                    serialized,
                ),
            )
        return {**row, "version": expected + 1, "state": state}
