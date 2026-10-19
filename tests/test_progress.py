"""Durable intent, restart, scoped reconciliation and secret-free audit evidence."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import httpx
import pytest
from creation_fixtures import approved, offline

from loremaster.creation import ApprovalGate, CreationScope, resolve_creation_request
from loremaster.kanka import KankaError
from loremaster.progress import CreationJournal, fingerprint


@pytest.fixture
async def durable(tmp_path, monkeypatch):
    async def no_network(*_args, **_kwargs):
        raise AssertionError("Real networking is forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    path = tmp_path / "private.sqlite3"
    opened = []

    def create(previous=None):
        store = CreationJournal(path)
        rehearsal = offline.OfflineCreation(progress_store=store, execution_id="test-1")
        if previous is not None:
            rehearsal.records = deepcopy(previous.records)
            rehearsal.attachments = deepcopy(previous.attachments)
        opened.append((store, rehearsal))
        return rehearsal

    yield path, create
    for store, rehearsal in opened:
        await rehearsal.aclose()
        store.close()


async def test_intent_is_committed_before_every_send_and_tokens_are_not_stored(durable):
    path, create = durable
    rehearsal = create()
    original = rehearsal._handle

    def checked(request):
        if request.method == "POST":
            with sqlite3.connect(path) as db:
                row = db.execute(
                    "SELECT state, fingerprint FROM steps WHERE key = ?",
                    (rehearsal._active_key,),
                ).fetchone()
            assert row == (
                "intent",
                fingerprint(
                    request.url.path.removeprefix("/1.0/"), json.loads(request.content)
                ),
            )
        return original(request)

    await rehearsal._writer.aclose()
    rehearsal._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(checked)
    )
    grant = approved(rehearsal, offline.example_draft())
    result = await rehearsal.run(grant)
    assert result["status"] == "verified"
    assert len(rehearsal.progress_store.progress("test-1")) == 5
    stored = path.read_bytes()
    assert grant.token.encode() not in stored
    assert b"offline-dummy-token" not in stored
    assert path.stat().st_mode & 0o777 == 0o600
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); "
            'print(db.execute("SELECT COUNT(*) FROM steps '
            "WHERE state='confirmed'\").fetchone()[0])",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert process.stdout.strip() == "5"


async def test_restart_resumes_known_rejection_only_with_new_approval(durable):
    _path, create = durable
    first = create()
    draft = offline.example_draft()
    grant = approved(first, draft)
    first.failure = ("post:1", 403)
    partial = await first.run(grant)
    assert partial["status"] == "partial"
    await first.aclose()
    first.progress_store.close()
    restored = create(first)
    with pytest.raises(PermissionError):
        await restored.run(grant)
    assert restored.requests == []
    result = await restored.run(approved(restored, draft))
    assert result["status"] == "verified"
    assert result["entity_id"] == partial["entity_id"]
    assert [method for method, _path in restored.requests if method != "GET"] == [
        "POST"
    ]
    assert len(restored.records) == 3
    assert len(restored.attachments[result["entity_id"]]["posts"]) == 2


@pytest.mark.parametrize("step", ["character", "post:0"])
async def test_lost_response_survives_restart_and_requires_reviewed_matching_id(
    durable, step
):
    _path, create = durable
    first = create()
    draft = offline.example_draft()
    first.lose_response = step
    uncertain = await first.run(approved(first, draft))
    assert uncertain["status"] == "uncertain"
    await first.aclose()
    first.progress_store.close()
    restored = create(first)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await restored.run(approved(restored, draft))
    assert restored.requests == []
    uid = next(
        uid for uid, row in restored.records.items() if row["name"] == draft.name
    )
    candidate = (
        uid if step == "character" else restored.attachments[uid]["posts"][0]["id"]
    )
    mismatch = await restored.reconcile(
        201 if step == "character" else 9999, reviewed_by="Reviewer"
    )
    assert mismatch["status"] in ("mismatch", "missing_or_ambiguous")
    assert "uncertain" in restored.progress_store.progress("test-1")
    inspected = await restored.reconcile(candidate)
    assert inspected["status"] == "matching_candidate"
    assert "uncertain" in restored.progress_store.progress("test-1")
    confirmed = await restored.reconcile(
        candidate, reviewed_by="trusted-offline-reviewer"
    )
    assert confirmed["status"] == "confirmed"
    assert all(method == "GET" for method, _path in restored.requests)
    result = await restored.run(approved(restored, draft))
    assert result["status"] == "verified"
    assert result["entity_id"] == uid
    assert len(restored.records) == 3
    assert len(restored.attachments[uid]["posts"]) == 2
    count = sum(
        method == "POST" for method, _path in first.requests + restored.requests
    )
    assert count == 5


async def test_crash_between_response_and_recording_preserves_uncertain_intent(
    durable, monkeypatch
):
    _path, create = durable
    first = create()
    original = first.progress_store.finish

    def crash(execution_id, key, state, result=None):
        if key == "post:0":
            raise RuntimeError("Simulated process termination")
        original(execution_id, key, state, result)

    monkeypatch.setattr(first.progress_store, "finish", crash)
    with pytest.raises(RuntimeError, match="process termination"):
        await first.run(approved(first, offline.example_draft()))
    assert first.progress_store.pending("test-1")["state"] == "intent"
    await first.aclose()
    first.progress_store.close()
    restored = create(first)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await restored.run(approved(restored, offline.example_draft()))
    assert restored.requests == []


@pytest.mark.parametrize("change", ["entry", "campaign", "references"])
async def test_execution_id_cannot_be_rebound_to_changed_content(durable, change):
    _path, create = durable
    rehearsal = create()
    draft = offline.example_draft()
    gate = rehearsal.gate
    context = gate.claim(approved(rehearsal, draft))
    store = rehearsal.progress_store
    store.bind("test-1", context, {201: 21})
    if change == "entry":
        context = gate.claim(
            approved(rehearsal, draft.model_copy(update={"entry": "Changed"}))
        )
    elif change == "campaign":
        gate = ApprovalGate(
            CreationScope(
                campaign_ids=frozenset({8}),
                operations=frozenset(
                    {
                        "character.create",
                        "relation.create",
                        "inventory.add",
                        "post.create",
                    }
                ),
            )
        )
        changed = draft.model_copy(update={"campaign_id": 8})
        preview = gate.prepare(changed)
        context = gate.claim(
            gate.approve(
                preview["proposal_id"],
                reviewed_digest=preview["content_digest"],
                approved_by="Reviewer",
            )
        )
    with pytest.raises(ValueError, match="different content or references"):
        store.bind("test-1", context, {201: 22 if change == "references" else 21})
    assert store.bind("another-execution", context, {201: 21}) == {}
    assert rehearsal.requests == []


async def test_restored_completed_attachment_ids_are_verified(durable):
    _path, create = durable
    first = create()
    first.failure = ("post:1", 403)
    result = await first.run(approved(first, offline.example_draft()))
    first.attachments[result["entity_id"]]["posts"][0]["id"] = 999
    await first.aclose()
    restored = create(first)
    with pytest.raises(KankaError, match="record IDs changed"):
        await restored.run(approved(restored, offline.example_draft()))
    assert all(method == "GET" for method, _path in restored.requests)


@pytest.mark.parametrize("change", ["body", "path", "step", "coercion"])
async def test_persisted_request_must_match_reviewed_content(durable, change):
    _path, create = durable
    rehearsal = create()
    draft = offline.example_draft()
    approved_context = rehearsal.gate.claim(approved(rehearsal, draft))
    store = rehearsal.progress_store
    store.bind("test-1", approved_context, {201: 21})
    path, body = resolve_creation_request(draft, "character")
    if change == "body":
        body["is_private"] = False
    elif change == "coercion":
        body["is_private"] = 1
    elif change == "path":
        path = path.replace("7", "8")
    with pytest.raises(ValueError):
        store.begin(
            "test-1",
            approved_context,
            "delete" if change == "step" else "character",
            path,
            body,
        )
    assert store.progress("test-1") == {}


@pytest.mark.parametrize("value", ["", "../escape", "é", True, None, "x" * 129])
def test_invalid_execution_ids_fail_before_storage_reads(tmp_path, value):
    store = CreationJournal(tmp_path / "private.sqlite3")
    try:
        with pytest.raises(ValueError, match="execution_id"):
            store.progress(value)
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["public_file", "symlink"])
def test_insecure_or_symlinked_journal_is_rejected(tmp_path, kind):
    target = tmp_path / "target"
    target.write_text("")
    path = tmp_path / "journal"
    if kind == "symlink":
        path.symlink_to(target)
    else:
        path.write_text("")
        path.chmod(0o644)
    with pytest.raises(PermissionError, match="private regular file"):
        CreationJournal(path)


async def test_two_workers_cannot_commit_intent_for_the_same_step(durable):
    path, create = durable
    rehearsal = create()
    draft = offline.example_draft()
    context = rehearsal.gate.claim(approved(rehearsal, draft))
    rehearsal.progress_store.bind("test-1", context, {201: 21})
    request_path, body = resolve_creation_request(draft, "character")
    barrier = Barrier(2)

    def attempt():
        store = CreationJournal(path)
        try:
            barrier.wait(timeout=5)
            try:
                store.begin("test-1", context, "character", request_path, body)
            except RuntimeError:
                return False
            return True
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _index: attempt(), range(2))) == [False, True]
    assert rehearsal.progress_store.progress("test-1") == {"uncertain": "character"}


@pytest.mark.parametrize(
    "case", ["mismatch", "incomplete", "pagination", "fingerprint", "campaign"]
)
async def test_reconciliation_fails_closed_without_changing_progress(durable, case):
    _path, create = durable
    rehearsal = create()
    rehearsal.lose_response = "post:0"
    result = await rehearsal.run(approved(rehearsal, offline.example_draft()))
    uid = result["entity_id"]
    store = rehearsal.progress_store
    candidate = rehearsal.attachments[uid]["posts"][0]["id"]
    if case == "mismatch":
        rehearsal.attachments[uid]["posts"][0]["entry"] = "Changed after creation"
    elif case in ("incomplete", "pagination"):
        original = rehearsal._handle

        def no_pagination(request):
            response = original(request)
            if request.url.path.endswith("/posts"):
                if case == "incomplete":
                    return httpx.Response(403)
                return httpx.Response(200, json={"data": response.json()["data"]})
            return response

        await rehearsal.reader.aclose()
        rehearsal.reader = offline.KankaClient(
            "offline",
            allowed_campaign_ids=frozenset({7}),
            transport=httpx.MockTransport(no_pagination),
        )
    elif case == "fingerprint":
        with store._db:
            store._db.execute("UPDATE steps SET body = '{}' WHERE key = 'post:0'")
    else:
        with store._db:
            store._db.execute(
                "UPDATE executions SET campaign_id = 8 WHERE id = 'test-1'"
            )
    before = list(rehearsal.requests)
    if case in ("fingerprint", "campaign", "pagination"):
        with pytest.raises((ValueError, PermissionError, KankaError)):
            await rehearsal.reconcile(candidate, reviewed_by="Reviewer")
        if case != "pagination":
            assert rehearsal.requests == before
    else:
        evidence = await rehearsal.reconcile(candidate, reviewed_by="Reviewer")
        assert evidence["status"] == case
    assert store.progress("test-1")["uncertain"] == "post:0"
    assert all(method == "GET" for method, _path in rehearsal.requests[len(before) :])


@pytest.mark.parametrize("reviewer", ["", "   ", True])
async def test_reconciliation_reviewer_is_validated_before_reading(durable, reviewer):
    _path, create = durable
    rehearsal = create()
    with pytest.raises(ValueError, match="reviewer"):
        await rehearsal.reconcile(1, reviewed_by=reviewer)
    assert rehearsal.requests == []


async def test_same_intent_cannot_be_resolved_with_changed_review_or_record(durable):
    _path, create = durable
    rehearsal = create()
    rehearsal.lose_response = "post:0"
    result = await rehearsal.run(approved(rehearsal, offline.example_draft()))
    store = rehearsal.progress_store
    row = store.pending("test-1")
    candidate = rehearsal.attachments[result["entity_id"]]["posts"][0]
    with pytest.raises(ValueError, match="Reviewed intent"):
        store.confirm_reconciled("test-1", "changed", candidate, reviewed_by="Reviewer")
    with pytest.raises(ValueError, match="differs"):
        store.confirm_reconciled(
            "test-1",
            row["fingerprint"],
            {**candidate, "entity_id": 999},
            reviewed_by="Reviewer",
        )
    assert store.pending("test-1")["state"] == "uncertain"
