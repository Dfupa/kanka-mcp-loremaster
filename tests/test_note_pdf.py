"""Actual PDF workers, extraction warnings, immutable variants and TXT migration."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from pdf_fixture import digital_pdf, pdf_bytes

from loremaster.kanka import KankaClient
from loremaster.note_models import ImportContext, NoteAnalysis
from loremaster.note_pdf import extract_pdf
from loremaster.note_store import NoteStore
from loremaster.note_text import correct_text
from loremaster.session_notes import SessionNoteService

ROOT = Path(__file__).resolve().parents[1]


def test_digital_pdf_preserves_page_text_glyphs_regions_and_quote_guards():
    raw = digital_pdf()
    result = extract_pdf(raw)
    metadata, blocks = result["metadata"], result["blocks"]
    assert metadata["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["page_count"] == 2 and metadata["complete"]
    assert {b["page"] for b in blocks} == {1, 2}
    assert all(b["quote_fidelity"] == "extracted_text_only" for b in blocks)
    text = "".join(b["text"] for b in blocks)
    assert "Sesión 12 — 17/10/2026" in text
    assert "V dijo: «no pienso bolver»." in text
    quote = next(b for b in blocks if "bolver" in b["text"])
    assert quote["regions"] and quote["protected"]
    analysis = NoteAnalysis.model_validate(
        {
            "sessions": [],
            "analysed_blocks": [quote["id"]],
            "edits": [
                {
                    "anchor": {"block_id": quote["id"], "excerpt": "bolver"},
                    "replacement": "volver",
                    "reason": "Unsafe",
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="protected"):
        correct_text(blocks, analysis)


@pytest.mark.parametrize(
    "spec,expected",
    [
        ({"image": True}, "ocr_required"),
        ({}, "no_extractable_text"),
        (
            {"image": True, "lines": [(72, 720, "Visible text plus an image")]},
            "text_requires_review",
        ),
        (
            {"lines": [(72, 720, "Left column"), (350, 720, "Right column")]},
            "text_requires_review",
        ),
        (
            {"lines": [(72, 720, "Rotated page")], "rotation": 90},
            "text_requires_review",
        ),
        (
            {"lines": [(72, 720, "Uncertain font")], "missing_font": True},
            "text_requires_review",
        ),
    ],
)
def test_uncertain_or_missing_text_is_not_complete(spec, expected):
    result = extract_pdf(pdf_bytes([spec]))
    assert result["metadata"]["pages"][0]["status"] == expected
    assert not result["metadata"]["complete"]
    assert result["metadata"]["pages"][0]["warnings"]
    if spec.get("image") or not spec:
        assert result["metadata"]["ocr_requests"][0]["execution"] == "not_configured"


def test_repeated_headers_are_flagged_without_deleting_source():
    result = extract_pdf(
        pdf_bytes(
            [
                {
                    "lines": [
                        (72, 750, "Campaign notes"),
                        (72, 700, "First session text"),
                    ]
                },
                {
                    "lines": [
                        (72, 750, "Campaign notes"),
                        (72, 700, "Second session text"),
                    ]
                },
            ]
        )
    )
    assert "".join(b["text"] for b in result["blocks"]).count("Campaign notes") == 2
    assert any(
        "repeated_margin_text_candidate" in b["warnings"] for b in result["blocks"]
    )


def test_partial_ranges_are_explicit_and_have_distinct_immutable_variants(tmp_path):
    raw = digital_pdf()
    store = NoteStore(tmp_path / "notes.db")
    try:
        first = store.import_pdf(7, raw, ImportContext(), "sessions.pdf", page_count=1)
        assert first["extraction"]["next_page"] == 2
        assert first["extraction"]["omitted_ranges"] == [[2, 2]]
        second = store.import_pdf(
            7,
            raw,
            ImportContext(),
            "sessions.pdf",
            document_id=first["document_id"],
            start_page=2,
            page_count=1,
        )
        assert (
            second["id"] != first["id"]
            and second["document_id"] == first["document_id"]
        )
        assert {b["page"] for b in second["blocks"]} == {2}
        assert second["raw"] == raw and second["source_hash"] == first["source_hash"]
        replay = store.import_pdf(
            7,
            raw,
            ImportContext(),
            "sessions.pdf",
            document_id=first["document_id"],
            page_count=1,
        )
        assert replay["id"] == first["id"]
        assert store.load(7, first["id"])["extraction"]["next_page"] == 2
    finally:
        store.close()


def test_encrypted_and_malformed_sources_are_rejected_without_payloads():
    with pytest.raises(ValueError, match="encrypted"):
        extract_pdf(pdf_bytes([{}], encrypted=True))
    with pytest.raises(ValueError, match="parsed"):
        extract_pdf(b"%PDF-private-invalid-content")
    with pytest.raises(ValueError, match="header"):
        extract_pdf(b"not a PDF")
    with pytest.raises(ValueError, match="outside"):
        extract_pdf(digital_pdf(), start_page=8)


@pytest.mark.parametrize(
    "start,count", [(0, 1), (True, 1), (1, 0), (1, 257), (1, False)]
)
def test_page_range_bounds_are_runtime_guards(start, count):
    with pytest.raises(ValueError, match="PDF start page"):
        extract_pdf(digital_pdf(), start_page=start, page_count=count)


def test_optional_dependency_and_worker_timeout_errors(monkeypatch):
    import loremaster.note_pdf as module

    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ValueError, match=r"\[pdf\]"):
        extract_pdf(digital_pdf())
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: object())

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 1)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="timed out"):
        extract_pdf(digital_pdf())


def test_worker_drops_kanka_credentials_and_never_exposes_stderr(monkeypatch):
    import loremaster.note_pdf as module

    original = module.subprocess.run
    monkeypatch.setenv("KANKA_API_TOKEN", "offline-secret-do-not-forward")

    def checked(*args, **kwargs):
        assert not any(k.startswith("KANKA_") for k in kwargs["env"])
        assert "shell" not in kwargs
        return original(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", checked)
    result = extract_pdf(
        pdf_bytes([{"lines": [(72, 720, "Safe text")], "action": True}])
    )
    assert "offline-secret" not in json.dumps(result)


def test_old_txt_store_migrates_without_changing_sources_or_snapshots(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE documents (id TEXT PRIMARY KEY,campaign_id INTEGER,context TEXT);
        CREATE TABLE imports (id TEXT PRIMARY KEY,
        document_id TEXT REFERENCES documents(id),
        source_hash TEXT,label TEXT,method TEXT,encoding TEXT,raw BLOB,blocks TEXT,
        version INTEGER,UNIQUE(document_id,source_hash));
        CREATE TABLE snapshots (import_id TEXT REFERENCES imports(id),version INTEGER,
        base_version INTEGER,request_hash TEXT,state TEXT,
        PRIMARY KEY(import_id,version),
        UNIQUE(import_id,base_version,request_hash));
    """)
    db.execute(
        "INSERT INTO documents VALUES ('doc',7,?)", (ImportContext().model_dump_json(),)
    )
    db.execute(
        "INSERT INTO imports VALUES ('imp','doc','hash','old.txt','txt',NULL,?,?,1)",
        (
            b"old text",
            json.dumps(
                [{"id": "b-1", "text": "old text", "offset": 0, "protected": []}]
            ),
        ),
    )
    db.execute(
        "INSERT INTO snapshots VALUES ('imp',1,0,'request',?)",
        (json.dumps({"kept": "unchanged"}),),
    )
    db.commit()
    db.close()
    path.chmod(0o600)
    store = NoteStore(path)
    try:
        row = store.load(7, "imp")
        assert row["raw"] == b"old text" and row["state"] == {"kept": "unchanged"}
        assert row["version"] == 1 and row["extraction"] == {}
        assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


async def test_pdf_cli_import_preserves_bytes_and_reports_ocr_handoff(tmp_path):
    source = tmp_path / "sessions.pdf"
    raw = pdf_bytes([{"image": True}])
    source.write_bytes(raw)
    database = tmp_path / "notes.db"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loremaster.notes_cli",
            str(source),
            "--database",
            str(database),
            "--campaign",
            "7",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("KANKA_")},
    )
    receipt = json.loads(result.stdout)
    assert receipt["extraction"]["ocr_requests"][0]["page"] == 1
    assert not receipt["extraction"]["complete"]
    store = NoteStore(database)
    client = KankaClient("offline", transport=httpx.MockTransport(lambda r: None))
    try:
        row = store.load(7, receipt["id"])
        assert row["raw"] == raw
        service = SessionNoteService(client, store)
        assert not service.inspect(7, row["id"])["complete"]
        payload = NoteAnalysis.model_validate(
            {"sessions": [], "analysed_blocks": [row["blocks"][0]["id"]]}
        )
        with pytest.raises(ValueError, match="Unreadable"):
            service._validate_analysis(row, payload)
    finally:
        store.close()
        await client.aclose()


def test_dialogue_crossing_pdf_pages_remains_protected():
    result = extract_pdf(
        pdf_bytes(
            [
                {"lines": [(72, 720, "V dijo: «no pienso")]},
                {"lines": [(72, 720, "bolver».")]},
            ]
        )
    )
    block = next(b for b in result["blocks"] if "bolver" in b["text"])
    payload = NoteAnalysis.model_validate(
        {
            "sessions": [],
            "analysed_blocks": [block["id"]],
            "edits": [
                {
                    "anchor": {"block_id": block["id"], "excerpt": "bolver"},
                    "replacement": "volver",
                    "reason": "Unsafe",
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="protected"):
        correct_text(result["blocks"], payload)


def test_content_budget_is_an_explicit_failed_page():
    from loremaster.note_pdf import MAX_CONTENT_BYTES

    result = extract_pdf(pdf_bytes([{"content": b"q " * (MAX_CONTENT_BYTES // 2 + 1)}]))
    assert result["metadata"]["pages"][0]["status"] == "extraction_failed"
    assert result["metadata"]["complete"] is False


def test_invalid_worker_protocol_never_leaks_payload(monkeypatch):
    import loremaster.note_pdf as module

    def invalid(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, stdout=b"[]", stderr=b"PRIVATE PDF LOG"
        )

    monkeypatch.setattr(module.subprocess, "run", invalid)
    with pytest.raises(ValueError, match="invalid result") as error:
        extract_pdf(digital_pdf())
    assert "PRIVATE" not in str(error.value)
