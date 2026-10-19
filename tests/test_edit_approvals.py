"""Local edit approval boundary, configuration and GET-only reviewed recovery."""

import asyncio
import json
import subprocess
import sys

import httpx
import pytest
from test_editing import approve, draft
from test_editing import editor as editor

from loremaster import approve as command
from loremaster.edit_store import EditStore
from loremaster.kanka import KankaClient
from loremaster.server import creation_settings


async def test_edit_cli_refuses_headless_approval_without_printing_lore(
    editor, tmp_path
):
    service, _api = editor
    proposal = await service.prepare(draft())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loremaster.approve",
            "--database",
            str(tmp_path / "private.sqlite3"),
            "--kind",
            "edit",
            "approve",
            proposal["proposal_id"],
        ],
        input="approve\n",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "interactive local terminal" in result.stderr
    assert "New lore" not in result.stdout
    assert service.store.review(proposal["proposal_id"])["status"] == "pending"


@pytest.mark.parametrize("reply", ["wrong", "EOF"])
async def test_cancelled_edit_cli_approval_leaves_it_pending(
    editor, tmp_path, monkeypatch, reply
):
    service, _api = editor
    proposal = await service.prepare(draft())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "approve",
            "--database",
            str(tmp_path / "private.sqlite3"),
            "--kind",
            "edit",
            "approve",
            proposal["proposal_id"],
        ],
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def answer(_prompt):
        if reply == "EOF":
            raise EOFError
        return reply

    monkeypatch.setattr("builtins.input", answer)
    with pytest.raises(SystemExit, match="cancelled"):
        await asyncio.to_thread(command.main)
    assert service.store.review(proposal["proposal_id"])["status"] == "pending"


@pytest.mark.parametrize("digest", [None, "", "é", True, "forged"])
async def test_edit_digest_is_not_approval_authority(editor, digest):
    service, _api = editor
    proposal = await service.prepare(draft())
    with pytest.raises(PermissionError):
        service.store.approve(proposal["proposal_id"], digest, "Reviewer")
    with pytest.raises(PermissionError):
        service.store.claim(proposal["proposal_id"])


async def test_edit_pending_limit_and_payload_limit(editor, monkeypatch):
    service, _api = editor
    monkeypatch.setattr("loremaster.edit_store.MAX_PENDING_PROPOSALS", 1)
    first = await service.prepare(draft())
    with pytest.raises(ValueError, match="Too many"):
        await service.prepare(draft(changes={"age": "29"}))
    service.store.revoke(first["proposal_id"])
    assert (await service.prepare(draft(changes={"age": "29"})))["status"] == "pending"
    with pytest.raises(ValueError, match="128 KiB"):
        await service.prepare(draft(changes={"entry": "x" * (128 * 1024)}))


async def test_local_edit_reconciliation_gets_evidence_but_issues_no_approval(
    editor, tmp_path, monkeypatch
):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.lose_response = True
    await service.execute(7, proposal["proposal_id"])
    database = tmp_path / "private.sqlite3"
    for key, value in {
        "KANKA_CREATION_DATABASE": str(database),
        "KANKA_WRITE_CAMPAIGN_IDS": "7",
        "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
        "KANKA_WRITE_OPERATIONS": "character.update",
        "KANKA_API_TOKEN": "offline-reconciliation-token",
    }.items():
        monkeypatch.setenv(key, value)
    clients = []

    def read_client(token, **kwargs):
        reader = KankaClient(token, transport=httpx.MockTransport(api.handle), **kwargs)
        clients.append(reader)
        return reader

    monkeypatch.setattr(command, "KankaClient", read_client)
    checkpoint = len(api.requests)
    monkeypatch.setattr("builtins.input", lambda _prompt: "wrong")
    with pytest.raises(SystemExit, match="cancelled"):
        await command.reconcile_edit(service.store, database, proposal["proposal_id"])
    assert service.store.review(proposal["proposal_id"])["outcome"] == "uncertain"
    phrase = f"reconcile {proposal['proposal_id']} {proposal['review_digest']}"
    monkeypatch.setattr("builtins.input", lambda _prompt: phrase)
    await command.reconcile_edit(service.store, database, proposal["proposal_id"])
    review = service.store.review(proposal["proposal_id"])
    assert review["outcome"] == "confirmed" and review["status"] == "claimed"
    assert review["reconciled_by"]
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
    assert all(reader._http.is_closed for reader in clients)
    with pytest.raises(PermissionError):
        service.store.claim(proposal["proposal_id"])


async def test_reconciliation_stale_fingerprint_never_confirms(editor):
    service, api = editor
    proposal = await service.prepare(draft())
    approve(service, proposal)
    api.lose_response = True
    await service.execute(7, proposal["proposal_id"])
    with pytest.raises(PermissionError):
        service.store.confirm_reconciled(proposal["proposal_id"], "forged", "Reviewer")
    assert service.store.review(proposal["proposal_id"])["outcome"] == "uncertain"


async def test_creation_and_edit_proposal_ids_cannot_be_interchanged(editor, tmp_path):
    service, _api = editor
    proposal = await service.prepare(draft())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loremaster.approve",
            "--database",
            str(tmp_path / "private.sqlite3"),
            "review",
            proposal["proposal_id"],
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "New lore" not in result.stdout
    assert service.store.review(proposal["proposal_id"])["status"] == "pending"


@pytest.mark.parametrize(
    "operations",
    [
        "character.update",
        "post.update,inventory.update,relation.update,tag.update",
        "character.create,character.update",
    ],
)
def test_explicit_edit_operation_configuration_is_accepted(
    tmp_path, monkeypatch, operations
):
    monkeypatch.setenv("KANKA_CREATION_DATABASE", str(tmp_path / "private.sqlite3"))
    monkeypatch.setenv("KANKA_WRITE_CAMPAIGN_IDS", "7")
    monkeypatch.setenv("KANKA_WRITE_OPERATIONS", operations)
    path, scope = creation_settings()
    assert path.is_absolute() and scope.campaign_ids == frozenset({7})
    assert scope.operations == frozenset(operations.split(","))


def test_edit_store_rejects_insecure_files(tmp_path):
    path = tmp_path / "insecure.sqlite3"
    path.write_text("")
    path.chmod(0o644)
    with pytest.raises(PermissionError, match="private"):
        EditStore(path)


async def test_invalid_stored_edit_does_not_disclose_private_payload(editor):
    service, _api = editor
    proposal = await service.prepare(draft())
    with service.store._db:
        service.store._db.execute(
            "UPDATE edit_proposals SET payload = ? WHERE id = ?",
            (json.dumps({"PRIVATE UPSTREAM DATA": True}), proposal["proposal_id"]),
        )
    with pytest.raises(PermissionError, match="integrity") as caught:
        service.store.review(proposal["proposal_id"])
    assert "PRIVATE UPSTREAM" not in str(caught.value)
