"""Local-file authority, exact review, bounded pending state and CLI behavior."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from creation_fixtures import offline

from loremaster import approvals, approve
from loremaster.approvals import LocalApprovals


@pytest.fixture
def local(tmp_path):
    path = tmp_path / "private.sqlite3"
    store = LocalApprovals(path)
    try:
        yield path, store
    finally:
        store.close()


def prepare(store):
    return store.prepare(
        offline.example_draft(), {"campaign": {"id": 7, "name": "Synthetic"}}
    )


@pytest.mark.parametrize("digest", [None, "", "é", True, "forged"])
def test_review_checksum_cannot_replace_host_approval(local, digest):
    _path, store = local
    proposal = prepare(store)
    with pytest.raises(PermissionError):
        store.approve(proposal["proposal_id"], digest, "Reviewer")
    with pytest.raises(PermissionError, match="approval"):
        store.claim(proposal["proposal_id"])


def test_review_copy_cannot_change_stored_content(local):
    _path, store = local
    proposal = prepare(store)
    proposal["preview"]["operations"][0]["body"]["entry"] = "Modified copy"
    proposal["references"]["campaign"]["id"] = 99
    store.approve(proposal["proposal_id"], proposal["review_digest"], "Reviewer")
    context = store.claim(proposal["proposal_id"])
    assert context.draft.entry == offline.example_draft().entry
    assert store.review(proposal["proposal_id"])["references"]["campaign"]["id"] == 7
    store.check(proposal["proposal_id"], context)
    with pytest.raises(PermissionError):
        store.claim(proposal["proposal_id"])
    store.revoke(proposal["proposal_id"])
    with pytest.raises(PermissionError):
        store.check(proposal["proposal_id"], context)


def test_pending_store_is_bounded_and_revocation_releases_a_slot(local, monkeypatch):
    _path, store = local
    monkeypatch.setattr(approvals, "MAX_PENDING_PROPOSALS", 1)
    first = prepare(store)
    with pytest.raises(ValueError, match="Too many"):
        prepare(store)
    store.revoke(first["proposal_id"])
    assert prepare(store)["status"] == "pending"


def test_two_process_connections_cannot_claim_one_approval(local):
    path, store = local
    proposal = prepare(store)
    store.approve(proposal["proposal_id"], proposal["review_digest"], "Reviewer")
    barrier = Barrier(2)

    def claim():
        connection = LocalApprovals(path)
        try:
            barrier.wait(timeout=5)
            try:
                connection.claim(proposal["proposal_id"])
                return True
            except PermissionError:
                return False
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _index: claim(), range(2))) == [False, True]


def test_cli_refuses_headless_approval_without_dumping_private_content(local):
    path, store = local
    proposal = prepare(store)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "loremaster.approve",
            "--database",
            str(path),
            "approve",
            proposal["proposal_id"],
        ],
        input="approve\n",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert process.returncode != 0
    assert "interactive local terminal" in process.stderr
    assert proposal["preview"]["operations"][0]["body"]["entry"] not in process.stdout
    assert store.review(proposal["proposal_id"])["status"] == "pending"


@pytest.mark.parametrize("response", ["wrong", "EOF"])
def test_cli_cancellation_never_approves(local, monkeypatch, response):
    path, store = local
    proposal = prepare(store)
    monkeypatch.setattr(
        sys,
        "argv",
        ["approve", "--database", str(path), "approve", proposal["proposal_id"]],
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def input_response(_prompt):
        if response == "EOF":
            raise EOFError
        return response

    monkeypatch.setattr("builtins.input", input_response)
    with pytest.raises(SystemExit, match="cancelled"):
        approve.main()
    assert store.review(proposal["proposal_id"])["status"] == "pending"


def test_tampered_payload_is_never_returned_or_approved(local):
    _path, store = local
    proposal = prepare(store)
    with store._db:
        store._db.execute(
            "UPDATE proposals SET payload = ? WHERE id = ?",
            (json.dumps({"draft": "PRIVATE INVALID DATA"}), proposal["proposal_id"]),
        )
    with pytest.raises(PermissionError, match="integrity") as error:
        store.review(proposal["proposal_id"])
    assert "PRIVATE INVALID DATA" not in str(error.value)
