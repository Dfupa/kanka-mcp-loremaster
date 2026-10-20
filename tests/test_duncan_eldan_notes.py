"""Ground-truth evaluation of the supplied noisy notes, using real MCP tools."""

import hashlib
from html import unescape

import httpx
import pytest
from duncan_eldan_fixture import (
    BLOCKS,
    SOURCE,
    NarrativeAPI,
    host_analysis,
    host_plan,
)

from loremaster.approvals import LocalApprovals
from loremaster.creation import CreationScope
from loremaster.execution import CreationService
from loremaster.kanka import KankaClient
from loremaster.note_bridge import SessionNoteBridge
from loremaster.note_models import Anchor, ImportContext, NoteAnalysis, TextEdit
from loremaster.note_store import NoteStore
from loremaster.note_text import correct_text
from loremaster.server import create_server
from loremaster.session_notes import SessionNoteService


async def run_case(tmp_path, case, monkeypatch, *, prepare_ready=False):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No real network is permitted in the editorial trial")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    api = NarrativeAPI(case)
    client = KankaClient(
        "offline-only",
        allowed_campaign_ids=frozenset({7}),
        admin_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(api),
    )
    service = SessionNoteService(
        client, NoteStore(tmp_path / (case + ".db")), reads_per_call=8
    )
    creator = (
        CreationService(
            client,
            "offline-only",
            LocalApprovals(tmp_path / (case + "-authority.db")),
            CreationScope(
                campaign_ids=frozenset({7}),
                operations=frozenset(
                    {
                        "journal.create",
                        "post.create",
                        "character.create",
                        "location.create",
                        "item.create",
                    }
                ),
            ),
            transport=httpx.MockTransport(api),
        )
        if prepare_ready
        else None
    )
    bridge = SessionNoteBridge(service, creator) if creator else None
    server = create_server(client, creator, notes=service, note_bridge=bridge)

    async def call(tool, **kwargs):
        result = await server.call_tool(tool, {"campaign_id": 7, **kwargs})
        if result.isError:
            raise AssertionError(result.structuredContent)
        return result.structuredContent["data"]

    try:
        # File ingestion preserves the bytes; no evaluator Markdown enters the source.
        imported = service.store.import_bytes(
            7,
            SOURCE.read_bytes(),
            ImportContext(language="es", locale="dmy"),
            SOURCE.name,
        )
        analysis, pending_edits = host_analysis()
        result = await call(
            "submit_session_note_analysis",
            import_id=imported["id"],
            expected_version=0,
            analysis=analysis.model_dump(mode="json"),
        )
        continuations = 0
        while result["work_cursor"]:
            result = await call(
                "submit_session_note_analysis",
                import_id=imported["id"],
                expected_version=result["version"],
                continuation=result["work_cursor"],
            )
            continuations += 1
        row = service.store.load(7, imported["id"])
        plan = host_plan(row)
        planned = await call(
            "prepare_session_note_proposals",
            import_id=row["id"],
            expected_version=result["version"],
            evidence_digest=result["evidence_digest"],
            plan=plan.model_dump(mode="json"),
        )
        output, cursor = [], None
        while True:
            page = await call(
                "inspect_session_notes",
                import_id=row["id"],
                view="preview",
                cursor=cursor,
            )
            output.extend(page["data"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        memberships = {}
        evidence, cursor = [], None
        while True:
            page = await call(
                "inspect_session_notes",
                import_id=row["id"],
                view="evidence",
                cursor=cursor,
            )
            evidence.extend(page["data"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        for key in ("thieves", "explorers"):
            lookup = row["state"]["lookups"][key]
            if lookup["status"] == "resolved":
                memberships[key] = await client.get_organisation_members(
                    7, lookup["candidate"]["entity_id"]
                )
        handoffs = []
        if prepare_ready:
            for op in output:
                if op["kind"] == "operation" and op["status"] == "ready_for_review":
                    linked = await call(
                        "prepare_session_note_handoff",
                        import_id=row["id"],
                        expected_version=planned["version"],
                        preview_digest=planned["preview_digest"],
                        operation_key=op["key"],
                    )
                    review = creator.store.review(linked["proposal_id"])
                    handoffs.append({"operation_key": op["key"], "proposal": review})
        return {
            "summary": planned,
            "row": row,
            "analysis": analysis,
            "plan": plan,
            "output": output,
            "pending_edits": pending_edits,
            "requests": api.requests,
            "continuations": continuations,
            "memberships": memberships,
            "evidence": evidence,
            "handoffs": handoffs,
        }
    finally:
        service.close()
        if creator:
            await creator.aclose()
        await client.aclose()


@pytest.mark.parametrize(
    "case", ["matching", "conflicting", "duplicate", "denied", "public"]
)
async def test_editorial_ground_truth_and_safe_dispositions(
    tmp_path, monkeypatch, case
):
    result = await run_case(tmp_path, case, monkeypatch)
    row, output = result["row"], result["output"]
    assert row["raw"] == SOURCE.read_bytes()
    assert row["source_hash"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert result["continuations"] > 0
    assert all(r.method == "GET" for r in result["requests"])
    assert len(result["analysis"].analysed_blocks) == len(BLOCKS) == 60
    text = "".join(
        record["text"] for record in output if record["kind"] == "corrected_text"
    )
    quotes = [b["text"] for b in BLOCKS if b["text"].startswith(("D: ", "E: "))]
    assert len(quotes) == 20 and all(text.count(q) == 1 for q in quotes)
    assert "haciéndose pasar por inspectores" in text
    assert "No se le robó dinero ni se le atacó." in text
    assert result["pending_edits"]  # Report label false positives instead of bypassing.
    assert any(e["anchor"]["excerpt"] == "correcion" for e in result["pending_edits"])
    ops = {r["key"]: r for r in output if r["kind"] == "operation"}
    assert ops["journal-s1"]["payload"]["date"] == "2026-10-17"
    assert ops["journal-s2"]["payload"]["date"] == "2026-10-19"
    for key in ("journal-s1", "journal-s2"):
        assert ops[key]["payload"]["is_private"] is True
        assert "author_entity_id" not in ops[key]["payload"]
        assert ops[key]["in_world_date"] is None
    for key in ("quest-post-s1", "quest-post-s2"):
        assert ops[key]["target_entity_id"] == 201
        assert ops[key]["payload"]["visibility_id"] == 3
    assert "todavía atrapados" in unescape(ops["quest-post-s1"]["payload"]["entry"])
    assert "Ambos escapan" in unescape(ops["quest-post-s2"]["payload"]["entry"])
    assert not any(
        op["action"] == "create_entity" and op["entity_type"] == "quest"
        for op in ops.values()
    )
    assert not any(
        op["action"] == "create_entity"
        and op["payload"].get("name")
        in ("Duncan", "Éldan", "Torn", "Gremio de Exploradores", "Sacred Gem")
        for op in ops.values()
    )
    for key in (
        "guild-memberships",
        "family-correction",
        "gem-inventory",
        "quest-status",
        "maze-hierarchy",
    ):
        assert ops[key]["status"] == "deferred"
    claims = {c.key: c for c in result["analysis"].claims}
    assert (
        claims["lock-failure"].mode == "failed"
        and claims["lock-failure"].polarity == "negative"
    )
    assert (
        claims["vampire-rumor"].mode == "rumor"
        and claims["vampire-rumor"].attribution == "eldan2"
    )
    assert claims["compass-joke"].mode == "dialogue"
    assert claims["future-delivery"].mode == "hypothetical"
    assert claims["no-delivery"].polarity == "negative"
    lookups = row["state"]["lookups"]
    bone_suggestions = [
        record
        for record in result["evidence"]
        if record.get("kind") == "entity_creation_suggestion"
        and record["mention_key"] == "bone"
    ]
    if case == "matching":
        assert not bone_suggestions
    else:
        assert len(bone_suggestions) == 1
        assert bone_suggestions[0]["name"] == "Lord Bone"
        assert bone_suggestions[0]["entity_type"] == "character"
        assert bone_suggestions[0]["approval_required"] is True
        assert ops["new-bone"]["status"] == "ready_for_review"
    if case == "matching":
        assert not any(key.startswith("new-") for key in ops)
        assert ops["duncan-age"]["status"] == "already_represented"
        assert len(result["memberships"]["thieves"]["data"]) == 2
    else:
        assert ops["duncan-age"]["status"] == "blocked"
        assert len([key for key in ops if key.startswith("new-")]) == 4
    if case == "duplicate":
        assert (
            lookups["duncan1"]["status"] == lookups["duncan2"]["status"] == "ambiguous"
        )
        assert ops["duncan-background"]["status"] == "blocked"
    if case == "denied":
        assert lookups["thieves"]["status"] == "inaccessible"
        assert result["summary"]["complete"] is False
        assert "PRIVATE ERROR PAYLOAD" not in str(output)
    if case == "conflicting":
        assert ops["duncan-age"]["diff"]["age"] == {
            "before": "45",
            "after": "26",
            "before_id_kind": None,
        }
        assert len(result["memberships"]["explorers"]["data"]) == 2
        assert not result["memberships"]["thieves"]["data"]
        assert any(
            a["state"] == "contradiction" for a in ops["duncan-age"]["assessments"]
        )


def test_golden_dialogue_and_label_edits_remain_protected():
    analysis, _ = host_analysis()
    for excerpt in ("se a dado", "correcion"):
        block = next(b for b in BLOCKS if excerpt in b["text"])
        changed = analysis.model_dump(mode="json")
        changed["edits"] = [
            TextEdit(
                anchor=Anchor(block_id=block["id"], excerpt=excerpt),
                replacement="changed",
                reason="Must require review",
            ).model_dump()
        ]
        with pytest.raises(ValueError, match="protected"):
            correct_text(BLOCKS, NoteAnalysis.model_validate(changed))


async def test_fixture_prepares_supported_writes_without_granting_approval(
    tmp_path, monkeypatch
):
    result = await run_case(tmp_path, "conflicting", monkeypatch, prepare_ready=True)
    keys = {link["operation_key"] for link in result["handoffs"]}
    assert keys == {
        "journal-s1",
        "journal-s2",
        "quest-post-s1",
        "quest-post-s2",
        "new-bone",
        "new-estate",
        "new-maze",
        "new-gem",
    }
    for link in result["handoffs"]:
        proposal = link["proposal"]
        assert proposal["status"] == "pending"
        assert proposal["reviewed_by"] is None and proposal["progress"] == {}
        assert proposal["provenance"]["source_revision"] == result["row"]["source_hash"]
    assert all(r.method == "GET" for r in result["requests"])
