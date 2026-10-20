"""Real stdio proposal generation and token-free local CLI ingestion."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from note_fixture import TEXT, analysis, plan
from pdf_fixture import digital_pdf

from loremaster.note_models import ImportContext
from loremaster.note_store import NoteStore

ROOT = Path(__file__).resolve().parents[1]


async def test_actual_stdio_import_analysis_evidence_and_preview(tmp_path):
    database = tmp_path / "notes.sqlite3"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests/note_stdio_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-token-only",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_NOTES_DATABASE": str(database),
        },
    )
    async with asyncio.timeout(30):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert len(tools) == 12
                for tool in tools:
                    if tool.name in (
                        "import_session_notes",
                        "submit_session_note_analysis",
                        "prepare_session_note_proposals",
                    ):
                        assert tool.annotations.readOnlyHint is False
                    assert "approve" not in tool.name and "execute" not in tool.name

                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError, result
                    assert (
                        json.loads(result.content[0].text) == result.structuredContent
                    )
                    return result.structuredContent["data"]

                imported = await call(
                    "import_session_notes",
                    {"campaign_id": 7, "text": TEXT, "context": {"locale": "dmy"}},
                )
                iid = imported["import_id"]
                source = await call(
                    "inspect_session_notes", {"campaign_id": 7, "import_id": iid}
                )
                assert "untrusted" in source["phase_instructions"]
                assert "".join(b["text"] for b in source["data"]) == TEXT
                analysed = await call(
                    "submit_session_note_analysis",
                    {
                        "campaign_id": 7,
                        "import_id": iid,
                        "expected_version": 0,
                        "analysis": analysis(),
                    },
                )
                assert analysed["complete"]
                evidence = await call(
                    "inspect_session_notes",
                    {"campaign_id": 7, "import_id": iid, "view": "evidence"},
                )
                assert len([r for r in evidence["data"] if "text" in r]) == 3
                assert (
                    len(
                        [
                            r
                            for r in evidence["data"]
                            if r.get("kind") == "identity_resolution"
                        ]
                    )
                    == 5
                )
                prepared = await call(
                    "prepare_session_note_proposals",
                    {
                        "campaign_id": 7,
                        "import_id": iid,
                        "expected_version": analysed["version"],
                        "evidence_digest": analysed["evidence_digest"],
                        "plan": plan(),
                    },
                )
                assert prepared["preview_available"]
                preview = await call(
                    "inspect_session_notes",
                    {"campaign_id": 7, "import_id": iid, "view": "preview"},
                )
                assert (
                    len(
                        [
                            item
                            for item in preview["data"]
                            if item["kind"] == "operation"
                        ]
                    )
                    == 6
                )
                assert any(
                    "no pienso bolver" in item.get("text", "")
                    for item in preview["data"]
                )
                denied = await session.call_tool(
                    "inspect_session_notes", {"campaign_id": 8, "import_id": iid}
                )
                assert denied.isError and denied.structuredContent["data"] is None
    assert b"offline-token-only" not in database.read_bytes()


def test_cli_exact_bytes_without_token_and_reimport(tmp_path):
    source = tmp_path / "session.txt"
    raw = b"\xef\xbb\xbf" + TEXT.encode()
    source.write_bytes(raw)
    database = tmp_path / "notes.db"
    command = [
        sys.executable,
        "-m",
        "loremaster.notes_cli",
        str(source),
        "--database",
        str(database),
        "--campaign",
        "7",
        "--language",
        "es",
    ]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("KANKA_")}
    first = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True, check=True
    )
    row = json.loads(first.stdout)
    second = subprocess.run(
        [*command, "--document-id", row["document_id"]],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(second.stdout)["id"] == row["id"]
    assert "Vélar" not in first.stdout
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT raw FROM imports").fetchone()[0] == raw
        assert db.execute("SELECT count(*) FROM imports").fetchone()[0] == 1


def test_cli_rejects_malformed_pdf_without_claiming_extraction(tmp_path):
    source = tmp_path / "session.pdf"
    source.write_bytes(b"%PDF-example")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loremaster.notes_cli",
            str(source),
            "--database",
            str(tmp_path / "notes.db"),
            "--campaign",
            "7",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode != 0 and "PDF could not be parsed" in result.stderr
    assert not (tmp_path / "notes.db").exists()


async def test_actual_stdio_pdf_pages_corrections_and_journal_provenance(tmp_path):
    database = tmp_path / "pdf-notes.db"
    store = NoteStore(database)
    row = store.import_pdf(
        7, digital_pdf(), ImportContext(locale="dmy"), "sessions.pdf"
    )
    store.close()
    iid = row["id"]
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests/note_stdio_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-pdf-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_ADMIN_CAMPAIGN_IDS": "7",
            "KANKA_NOTES_DATABASE": str(database),
        },
    )
    async with asyncio.timeout(30):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()

                async def call(name, **fields):
                    result = await session.call_tool(
                        name, {"campaign_id": 7, "import_id": iid, **fields}
                    )
                    assert not result.isError, result
                    return result.structuredContent["data"]

                extraction = await call("inspect_session_notes", view="extraction")
                assert [p["page"] for p in extraction["data"]] == [1, 2]
                source = await call("inspect_session_notes")
                blocks = source["data"]

                def anchored(word):
                    block = next(b for b in blocks if word in b["text"])
                    return {"block_id": block["id"], "excerpt": word}

                payload = {
                    "sessions": [
                        {
                            "key": "s12",
                            "label": "Sesión 12",
                            "block_ids": [b["id"] for b in blocks if b["page"] == 1],
                            "date_anchor": anchored("17/10/2026"),
                        },
                        {
                            "key": "s13",
                            "label": "Sesión 13",
                            "block_ids": [b["id"] for b in blocks if b["page"] == 2],
                            "date_anchor": anchored("19/10/2026"),
                        },
                    ],
                    "analysed_blocks": [b["id"] for b in blocks],
                    "edits": [
                        {
                            "anchor": anchored("llego"),
                            "replacement": "llegó",
                            "reason": "Accent correction",
                        }
                    ],
                    "claims": [
                        {
                            "key": "arrived",
                            "session_key": "s12",
                            "anchors": [anchored("L llego al puente.")],
                            "text": "L llegó al puente.",
                        },
                        {
                            "key": "guide",
                            "session_key": "s13",
                            "anchors": [anchored("Mara nos enseño el paso.")],
                            "text": "Mara mostró el paso.",
                        },
                    ],
                }
                analysed = await call(
                    "submit_session_note_analysis", expected_version=0, analysis=payload
                )
                assert analysed["complete"]
                plan_data = {
                    "assessments": [],
                    "operations": [
                        {
                            "key": "journal12",
                            "action": "create_entity",
                            "entity_type": "journal",
                            "session_key": "s12",
                            "claim_keys": ["arrived"],
                            "fields": {"name": "PDF session 12"},
                            "session_text": True,
                        },
                        {
                            "key": "journal13",
                            "action": "create_entity",
                            "entity_type": "journal",
                            "session_key": "s13",
                            "claim_keys": ["guide"],
                            "fields": {"name": "PDF session 13"},
                            "session_text": True,
                        },
                    ],
                }
                await call(
                    "prepare_session_note_proposals",
                    expected_version=analysed["version"],
                    evidence_digest=analysed["evidence_digest"],
                    plan=plan_data,
                )
                preview = await call("inspect_session_notes", view="preview")
                ops = [
                    entry for entry in preview["data"] if entry["kind"] == "operation"
                ]
                assert len(ops) == 2 and all(
                    op["status"] == "ready_for_review" for op in ops
                )
                assert ops[0]["source_refs"][0]["page"] == 1
                assert ops[1]["source_refs"][0]["page"] == 2
                assert all(
                    op["source_import_id"] == iid and op["extraction_variant"]
                    for op in ops
                )
                assert "no pienso bolver" in ops[0]["payload"]["entry"]
