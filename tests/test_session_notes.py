"""Source fidelity, campaign scoping, durable previews and strict GET boundaries."""

import json
import sqlite3
from copy import deepcopy

import httpx
import pytest
from note_fixture import TEXT, NoteAPI, analysis, anchor, plan

from loremaster.kanka import KankaClient, KankaError
from loremaster.note_models import Anchor, ImportContext, NoteAnalysis, NotePlan
from loremaster.note_store import NoteStore
from loremaster.note_text import (
    correct_text,
    decode_source,
    locate,
    session_date,
    source_blocks,
)
from loremaster.session_notes import SessionNoteService


@pytest.fixture
async def notes(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Real networking is forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    api = NoteAPI()
    client = KankaClient(
        "offline-only",
        allowed_campaign_ids=frozenset({7}),
        admin_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(api),
    )
    store = NoteStore(tmp_path / "notes.sqlite3")
    service = SessionNoteService(client, store)
    try:
        yield service, api
    finally:
        service.close()
        await client.aclose()


async def prepared(service):
    imported = service.import_text(7, TEXT, ImportContext(locale="dmy"))
    data = await service.submit(
        7, imported["import_id"], 0, NoteAnalysis.model_validate(analysis())
    )
    while data["work_cursor"]:
        data = await service.submit(
            7, data["import_id"], data["version"], continuation=data["work_cursor"]
        )
    return data


async def test_complete_two_session_workflow_and_grouped_posts(notes):
    service, api = notes
    data = await prepared(service)
    assert data["complete"] and data["pending_reads"] == 0
    searches = [
        r.url.params["name"] for r in api.requests if r.url.path.endswith("/entities")
    ]
    assert searches.count("Recuperar la llave") == 1
    result = await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(plan()),
    )
    output = service.inspect(7, data["import_id"], "preview")["data"]
    text = "".join(row["text"] for row in output if row["kind"] == "corrected_text")
    assert "L llegó" in text and "Mara nos enseñó" in text
    assert "V dijo: «no pienso bolver, ni por mil monedas».\r\n" in text
    ops = [row for row in output if row["kind"] == "operation"]
    assert len(ops) == 6
    assert all(row["status"] == "ready_for_review" for row in ops)
    first_post = next(row for row in ops if row["key"] == "post12")
    assert first_post["source_operations"] == ["post12", "dialogue12"]
    assert "no pienso bolver" in first_post["payload"]["entry"]
    assert (
        next(row for row in ops if row["key"] == "child")["payload"]["parent_entity_id"]
        == 201
    )
    assert (
        next(row for row in ops if row["key"] == "journal12")["payload"]["date"]
        == "2026-10-17"
    )
    replay = await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(plan()),
    )
    assert replay == result
    assert all(request.method == "GET" for request in api.requests)


@pytest.mark.parametrize(
    "raw,encoding",
    [(b"\xff", None), (b"", None), (b"a\x00b", None), (b"abc", "unknown-encoding")],
)
def test_decoding_rejects_lossy_or_invalid_input(raw, encoding):
    with pytest.raises(ValueError):
        decode_source(raw, encoding)


def test_exact_source_anchors_and_dialogue_guards():
    blocks = source_blocks(TEXT)
    assert "".join(b["text"] for b in blocks) == TEXT
    payload = analysis()
    payload["edits"].append(
        {"anchor": anchor(4, "bolver"), "replacement": "volver", "reason": "Unsafe"}
    )
    with pytest.raises(ValueError, match="protected"):
        correct_text(blocks, NoteAnalysis.model_validate(payload))
    with pytest.raises(ValueError, match="exactly once"):
        locate(Anchor(block_id="b-1", excerpt="a"), {"b-1": {"text": "a a"}})
    assert locate(
        Anchor(block_id="b-1", excerpt="a", before="a "), {"b-1": {"text": "a a"}}
    ) == (2, 3)


@pytest.mark.parametrize(
    "literal,locale,expected",
    [
        ("03/04/2026", None, None),
        ("03/04/2026", "dmy", "2026-04-03"),
        ("03/04/2026", "mdy", "2026-03-04"),
        ("2026-10-17", None, "2026-10-17"),
        ("8 de Ceniza, 412", None, None),
        ("31/02/2026", "dmy", None),
    ],
)
def test_dates_preserve_ambiguity_and_calendar_separation(literal, locale, expected):
    assert (
        session_date(
            Anchor(block_id="b", excerpt=literal), {"b": {"text": literal}}, locale
        )
        == expected
    )


async def test_resume_budgets_replay_and_restart(notes, tmp_path):
    service, _ = notes
    service.reads_per_call = 1
    imported = service.import_text(7, TEXT, ImportContext())
    data = await service.submit(
        7, imported["import_id"], 0, NoteAnalysis.model_validate(analysis())
    )
    assert data["pending_reads"] and not data["complete"]
    assert (
        await service.submit(
            7, imported["import_id"], 0, NoteAnalysis.model_validate(analysis())
        )
        == data
    )
    with pytest.raises(ValueError, match="conflict"):
        await service.submit(7, imported["import_id"], 0, continuation="changed")
    current = service.inspect(7, imported["import_id"])
    assert current["data"][0]["text"] == TEXT.splitlines(keepends=True)[0]
    reopened = NoteStore(tmp_path / "notes.sqlite3")
    try:
        other = SessionNoteService(service.client, reopened)
        assert other.inspect(7, imported["import_id"])["version"] == data["version"]
        with pytest.raises(ValueError, match="stale"):
            await other.submit(
                7,
                imported["import_id"],
                data["version"],
                continuation=data["work_cursor"],
            )
    finally:
        reopened.close()


async def test_campaign_scope_and_source_lineage(notes):
    service, _ = notes
    first = service.import_text(7, TEXT, ImportContext())
    repeated = service.import_text(
        7, TEXT, ImportContext(), document_id=first["document_id"]
    )
    assert repeated["import_id"] == first["import_id"]
    revised = service.import_text(
        7, TEXT + "Más notas.\n", ImportContext(), document_id=first["document_id"]
    )
    assert (
        revised["document_id"] == first["document_id"]
        and revised["import_id"] != first["import_id"]
    )
    with pytest.raises(PermissionError):
        service.inspect(8, first["import_id"])
    with pytest.raises(PermissionError):
        service.import_text(8, TEXT, ImportContext())


@pytest.mark.parametrize(
    "duplicate,failed,status",
    [(True, False, "ambiguous"), (False, True, "inaccessible")],
)
async def test_duplicate_or_inaccessible_entities_are_not_absent(
    notes, duplicate, failed, status
):
    service, api = notes
    api.duplicates, api.fail_search = duplicate, failed
    data = await prepared(service)
    state = service.store.load(7, data["import_id"])["state"]
    assert state["lookups"]["velar"]["status"] == status
    assert "PRIVATE RESPONSE" not in json.dumps(state)


@pytest.mark.parametrize(
    "case,status,suggest",
    [
        ("missing", "no_visible_match", True),
        ("exact", "resolved", False),
        ("partial", "ambiguous", False),
        ("duplicate", "ambiguous", False),
        ("wrong_type", "ambiguous", False),
        ("forbidden", "inaccessible", False),
        ("incomplete", "lookup_incomplete", False),
        ("unsupported", "no_visible_match", False),
        ("unknown_type", "no_visible_match", False),
        ("unsourced_alias", "ambiguous", False),
    ],
)
async def test_missing_entity_suggestion_requires_complete_supported_identity(
    notes, monkeypatch, case, status, suggest
):
    service, api = notes
    payload = analysis()
    if case in ("exact", "partial", "duplicate", "wrong_type"):
        api.cores[301] = (
            31,
            "quest" if case == "wrong_type" else "character",
            "Captain Mara" if case == "partial" else "Mara",
        )
    if case == "duplicate":
        api.cores[302] = (32, "character", "Mara")
    elif case == "forbidden":
        api.fail_search = True
    elif case == "incomplete":
        lookup = service.client.find_entities_by_name

        async def incomplete(campaign_id, name):
            if name == "Mara":
                raise KankaError("Listing incomplete", status_code=500)
            return await lookup(campaign_id, name)

        monkeypatch.setattr(service.client, "find_entities_by_name", incomplete)
    elif case in ("unsupported", "unknown_type"):
        payload["mentions"][-1]["entity_type"] = (
            "calendar" if case == "unsupported" else None
        )
    elif case == "unsourced_alias":
        payload["mentions"][-1]["name"] = "Unverified expansion"
    imported = service.import_text(7, TEXT, ImportContext(locale="dmy"))
    data = await service.submit(
        7, imported["import_id"], 0, NoteAnalysis.model_validate(payload)
    )
    while data["work_cursor"]:
        data = await service.submit(
            7, data["import_id"], data["version"], continuation=data["work_cursor"]
        )
    before = service.store.load(7, data["import_id"])
    requests_before = len(api.requests)
    records, cursor = [], None
    while True:
        page = service.inspect(7, data["import_id"], "evidence", cursor)
        records.extend(page["data"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    identity = next(
        row
        for row in records
        if row.get("kind") == "identity_resolution" and row["mention_key"] == "mara"
    )
    assert identity["status"] == status
    suggestions = [
        row for row in records if row.get("kind") == "entity_creation_suggestion"
    ]
    assert bool(suggestions) is suggest
    if suggest:
        assert len(suggestions) == 1
        assert suggestions[0]["name"] == "Mara"
        assert suggestions[0]["entity_type"] == "character"
        assert suggestions[0]["source_anchor"] == Anchor.model_validate(
            payload["mentions"][-1]["anchor"]
        ).model_dump(mode="json")
        assert suggestions[0]["is_private"] is True
        assert suggestions[0]["approval_required"] is True
    if case == "partial":
        assert any(
            row.get("kind") == "identity_candidate"
            and row["mention_key"] == "mara"
            and row["match_type"] == "partial"
            and row["name"] == "Captain Mara"
            for row in records
        )
    assert len(api.requests) == requests_before
    assert service.store.load(7, data["import_id"]) == before
    assert not data["preview_available"]
    assert all(request.method == "GET" for request in api.requests)


@pytest.mark.parametrize(
    "change", ["quote", "alias", "unknown_claim", "date_scope", "duplicate_session"]
)
async def test_invalid_analysis_is_atomic(notes, change):
    service, _ = notes
    data = service.import_text(7, TEXT, ImportContext())
    payload = analysis()
    if change == "quote":
        payload["edits"] = [
            {"anchor": anchor(4, "bolver"), "replacement": "volver", "reason": "No"}
        ]
    elif change == "alias":
        payload["mentions"][0]["alias_anchor"] = anchor(2, "V = Vélar.")
    elif change == "unknown_claim":
        payload["claims"][0]["subject"] = "invented"
    elif change == "date_scope":
        payload["sessions"][0]["date_anchor"] = anchor(8, "19/10/2026")
    else:
        payload["sessions"].append(deepcopy(payload["sessions"][0]))
    with pytest.raises(ValueError):
        await service.submit(
            7, data["import_id"], 0, NoteAnalysis.model_validate(payload)
        )
    assert service.store.load(7, data["import_id"])["version"] == 0


@pytest.mark.parametrize(
    "change",
    ["fake_id", "cycle", "bad_citation", "visibility", "unresolved", "unsupported"],
)
async def test_plan_guards_and_no_execution(notes, change):
    service, _ = notes
    data = await prepared(service)
    payload = plan()
    if change == "fake_id":
        payload["operations"][0]["fields"]["author_id"] = 999
    elif change == "cycle":
        payload["operations"][0]["references"] = [
            {
                "field": "author_entity_id",
                "target": {"kind": "proposal", "key": "journal12"},
            }
        ]
    elif change == "bad_citation":
        payload["assessments"] = [
            {
                "claim_key": "arrival",
                "state": "confirmed_canon",
                "explanation": "Claim",
                "citations": [{"evidence_id": "invented", "excerpt": "false"}],
            }
        ]
    elif change == "visibility":
        payload["operations"][0]["audience"] = "all"
    elif change == "unresolved":
        payload["assessments"] = [
            {
                "claim_key": "arrival",
                "state": "contradiction",
                "explanation": "Needs review",
            }
        ]
    else:
        payload["operations"][0]["entity_type"] = "race"
        payload["operations"][0]["session_text"] = False
    if change in ("fake_id", "cycle", "bad_citation"):
        with pytest.raises(ValueError):
            await service.prepare(
                7,
                data["import_id"],
                data["version"],
                data["evidence_digest"],
                NotePlan.model_validate(payload),
            )
    else:
        await service.prepare(
            7,
            data["import_id"],
            data["version"],
            data["evidence_digest"],
            NotePlan.model_validate(payload),
        )
        first = next(
            x
            for x in service.inspect(7, data["import_id"], "preview")["data"]
            if x.get("key") == "journal12"
        )
        assert first["blockers"] and first["status"] != "ready_for_review"


def test_store_rejects_insecure_files_and_symlinks(tmp_path):
    path = tmp_path / "unsafe.db"
    path.touch(mode=0o644)
    with pytest.raises(PermissionError):
        NoteStore(path)
    link = tmp_path / "link.db"
    link.symlink_to(path)
    with pytest.raises(OSError):
        NoteStore(link)


def test_store_snapshots_compare_and_swap(tmp_path):
    store = NoteStore(tmp_path / "notes.db")
    try:
        row = store.import_bytes(7, b"notes", ImportContext(), "notes.txt")
        store.save(row, 0, "first", {"value": 1})
        with pytest.raises(ValueError, match="conflict"):
            store.save(row, 0, "second", {"value": 2})
        assert store.load(7, row["id"])["state"] == {"value": 1}
        assert store.db.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
    finally:
        store.close()


@pytest.mark.parametrize(
    "line,word",
    [
        ("V said: 'I aint goin'.\n", "aint"),
        ("V said: “I aint goin”.\n", "aint"),
        ("V: I aint goin.\n", "aint"),
        ("V said: ‘I aint goin’.\n", "aint"),
        ("V said: «I aint goin».\n", "aint"),
        ('V said: "I aint goin\n', "aint"),
    ],
)
def test_dialogue_styles_are_immutable(line, word):
    value = NoteAnalysis.model_validate(
        {
            "sessions": [],
            "analysed_blocks": ["b-1"],
            "edits": [
                {
                    "anchor": anchor(1, word),
                    "replacement": "am not",
                    "reason": "grammar",
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="protected"):
        correct_text(source_blocks(line), value)


def test_apostrophe_inside_word_is_not_a_quote():
    value = NoteAnalysis.model_validate(
        {
            "sessions": [],
            "analysed_blocks": ["b-1"],
            "edits": [
                {
                    "anchor": anchor(1, "didnt"),
                    "replacement": "didn't",
                    "reason": "grammar",
                }
            ],
        }
    )
    assert (
        correct_text(source_blocks("O'Neil didnt leave.\n"), value)["b-1"]
        == "O'Neil didn't leave.\n"
    )


async def test_quote_attribution_and_source_ranges_are_preserved(notes):
    service, _ = notes
    data = await prepared(service)
    await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(plan()),
    )
    post = next(
        x
        for x in service.inspect(7, data["import_id"], "preview")["data"]
        if x.get("key") == "post12"
    )
    assert "V dijo: «no pienso bolver" in post["payload"]["entry"]
    assert {a["claim_key"] for a in post["assessments"]} == {"rumor", "goal"}
    assert any(ref["block_id"] == "b-4" and ref["range"] for ref in post["source_refs"])


async def test_collection_pages_resume_and_complete(notes):
    service, api = notes
    api.long_posts = True
    service.reads_per_call = 1
    data = await prepared(service)
    state = service.store.load(7, data["import_id"])["state"]
    assert state["collections"]["201-posts"]["complete"] is True
    assert [
        int(r.url.params["page"]) for r in api.requests if r.url.path.endswith("/posts")
    ] == [1, 2]


async def test_lookup_timeout_retains_explicit_incomplete_state(notes, monkeypatch):
    service, _ = notes

    async def timeout(*args):
        raise TimeoutError()

    monkeypatch.setattr(service, "_lookup", timeout)
    data = await prepared(service)
    assert not data["complete"]
    assert all(
        value["status"] == "lookup_incomplete"
        for value in service.store.load(7, data["import_id"])["state"][
            "lookups"
        ].values()
    )


async def test_stale_evidence_and_model_dialogue_are_rejected(notes):
    service, _ = notes
    data = await prepared(service)
    with pytest.raises(ValueError, match="digest"):
        await service.prepare(
            7,
            data["import_id"],
            data["version"],
            "invented",
            NotePlan.model_validate(plan()),
        )
    payload = plan()
    payload["operations"][1]["body"] = [
        {"kind": "text", "text": "V said: «invented words»."}
    ]
    with pytest.raises(ValueError, match="dialogue"):
        await service.prepare(
            7,
            data["import_id"],
            data["version"],
            data["evidence_digest"],
            NotePlan.model_validate(payload),
        )
    assert service.inspect(7, data["import_id"])["version"] == data["version"]


async def test_changed_analysis_invalidates_old_preview(notes):
    service, _ = notes
    data = await prepared(service)
    first = await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(plan()),
    )
    changed = analysis()
    changed["edits"] = []
    newer = await service.submit(
        7, data["import_id"], first["version"], NoteAnalysis.model_validate(changed)
    )
    assert not newer["preview_available"]
    assert service.inspect(7, data["import_id"], "preview")["data"] == []


async def test_cursors_bind_snapshot_view_and_campaign(notes, monkeypatch):
    import loremaster.session_notes as module

    service, _ = notes
    data = service.import_text(7, TEXT, ImportContext())
    monkeypatch.setattr(module, "PAGE_BYTES", 300)
    page = service.inspect(7, data["import_id"])
    assert page["next_cursor"]
    assert (
        service.inspect(7, data["import_id"], cursor=page["next_cursor"])["data"]
        != page["data"]
    )
    with pytest.raises(ValueError, match="cursor"):
        service.inspect(7, data["import_id"], "questions", page["next_cursor"])
    await service.submit(
        7, data["import_id"], 0, NoteAnalysis.model_validate(analysis())
    )
    with pytest.raises(ValueError, match="cursor"):
        service.inspect(7, data["import_id"], cursor=page["next_cursor"])


def test_notes_store_cannot_open_existing_approval_or_other_application_database(
    tmp_path,
):
    path = tmp_path / "application.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE proposals (id TEXT)")
    path.chmod(0o600)
    with pytest.raises(PermissionError, match="separate"):
        NoteStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("proposals",)]


def test_source_block_budget_and_reencoding_are_explicit(tmp_path):
    with pytest.raises(ValueError, match="Too many source blocks"):
        source_blocks("x\n" * 4097)
    store = NoteStore(tmp_path / "notes.db")
    try:
        row = store.import_bytes(7, b"notes", ImportContext(), "notes.txt")
        with pytest.raises(ValueError, match="encoding"):
            store.import_bytes(
                7,
                b"notes",
                ImportContext(),
                "notes.txt",
                encoding="ascii",
                document_id=row["document_id"],
            )
    finally:
        store.close()


async def test_source_order_and_invented_dates_are_not_accepted(notes):
    service, _ = notes
    imported = service.import_text(7, TEXT, ImportContext())
    bad = analysis()
    bad["sessions"][0]["block_ids"].reverse()
    with pytest.raises(ValueError, match="source order"):
        await service.submit(
            7, imported["import_id"], 0, NoteAnalysis.model_validate(bad)
        )
    data = await prepared(service)
    bad_plan = plan()
    bad_plan["operations"][0]["fields"]["date"] = "2099-01-01"
    with pytest.raises(ValueError, match="typed references"):
        await service.prepare(
            7,
            data["import_id"],
            data["version"],
            data["evidence_digest"],
            NotePlan.model_validate(bad_plan),
        )


async def test_duplicate_post_claims_are_merged_without_repeating_body(notes):
    service, _ = notes
    data = await prepared(service)
    payload = plan()
    repeated = deepcopy(payload["operations"][1])
    repeated["key"] = "same-claim-again"
    payload["operations"].append(repeated)
    await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(payload),
    )
    first_post = next(
        row
        for row in service.inspect(7, data["import_id"], "preview")["data"]
        if row.get("key") == "post12"
    )
    assert first_post["payload"]["entry"].count("Vélar sospecha") == 1
    assert first_post["claim_keys"] == ["rumor", "goal"]


async def test_proposed_parent_ids_stay_separate_from_numeric_payloads(notes):
    service, _ = notes
    data = await prepared(service)
    payload = plan()
    child = deepcopy(payload["operations"][3])
    child["key"] = "grandchild"
    child["fields"]["name"] = "Otra etapa"
    child["references"] = [
        {"field": "parent_entity_id", "target": {"kind": "proposal", "key": "child"}}
    ]
    payload["operations"].append(child)
    await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(payload),
    )
    record = next(
        row
        for row in service.inspect(7, data["import_id"], "preview")["data"]
        if row.get("key") == "grandchild"
    )
    assert "parent_entity_id" not in record["payload"]
    assert record["dependencies"] == ["child"] and record["status"] == "blocked"


def test_runtime_id_validation_does_not_accept_booleans(tmp_path):
    store = NoteStore(tmp_path / "notes.db")
    try:
        with pytest.raises(ValueError, match="positive"):
            store.import_bytes(True, b"text", ImportContext(), "notes.txt")
    finally:
        store.close()


async def test_unsourced_acronym_is_a_candidate_without_stopping_other_work(notes):
    service, _ = notes
    imported = service.import_text(7, TEXT, ImportContext())
    payload = analysis()
    payload["mentions"][0].pop("alias_anchor")
    data = await service.submit(
        7, imported["import_id"], 0, NoteAnalysis.model_validate(payload)
    )
    identities = service.store.load(7, data["import_id"])["state"]["lookups"]
    assert identities["lia"]["status"] == "ambiguous"
    assert identities["q12"]["status"] == "resolved"
    assert "user review" in identities["lia"]["reason"]
    await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(plan()),
    )
    preview = service.inspect(7, data["import_id"], "preview")["data"]
    assert (
        next(row for row in preview if row.get("key") == "journal12")["status"]
        == "blocked"
    )
    assert (
        next(row for row in preview if row.get("key") == "post13")["status"]
        == "ready_for_review"
    )


def test_private_notes_cannot_be_created_inside_any_git_checkout(tmp_path, monkeypatch):
    import loremaster.note_store as module

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: elsewhere")

    def unexpected_open(*args, **kwargs):
        raise AssertionError(
            "Private database creation must be rejected before opening"
        )

    monkeypatch.setattr(module.os, "open", unexpected_open)
    with pytest.raises(ValueError, match="outside"):
        NoteStore(checkout / "notes.db")
    assert not (checkout / "notes.db").exists()
