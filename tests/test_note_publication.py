"""Reviewed non-admin publication with invented notes and mandatory HTTP mocks."""

import asyncio
import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from note_fixture import plan as editorial_plan
from pydantic import ValidationError
from test_note_bridge import TEXT, analysis, handoff, plan
from test_note_bridge import bridge as bridge
from test_session_notes import notes as notes

from loremaster.approvals import LocalApprovals
from loremaster.kanka import KankaError
from loremaster.note_models import ImportContext, NoteAnalysis, NotePlan
from loremaster.publication import publication_review

SECRET = "GM_SECRET_ONLY: the baron is a dragon."
ROOT = Path(__file__).resolve().parents[1]


def public_plan(keys=None):
    proposed = plan().model_dump(mode="json")
    for op in proposed["operations"]:
        op.update(
            audience="all",
            publication={
                "rationale": "Review this public copy and omitted private context."
            },
        )
        op["session_text"] = False
        if op["action"] in ("create_entity", "create_post") or op["key"] == "post-edit":
            op["body"] = [
                {"kind": "text", "text": "Recover the blue key. Élan arrived safely."}
            ]
    if keys is not None:
        proposed["operations"] = [
            op for op in proposed["operations"] if op["key"] in keys
        ]
    return NotePlan.model_validate(proposed)


async def public_preview(service, api, proposed=None, *, audience="admin", secret=True):
    service.notes.client._admin_campaign_ids = frozenset()
    api.records[101]["is_private"] = False
    api.records[602]["is_private"] = False
    text = (
        TEXT.replace("Recover the blue key.", "Recover the blue key. " + SECRET)
        if secret
        else TEXT
    )
    imported = service.notes.import_text(
        7, text, ImportContext(locale="dmy", audience=audience)
    )
    data = await service.notes.submit(7, imported["import_id"], 0, analysis())
    while data["work_cursor"]:
        data = await service.notes.submit(
            7, data["import_id"], data["version"], continuation=data["work_cursor"]
        )
    return await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        proposed or public_plan(),
    )


def review_link(service, link):
    supervisor = service.creation if link["service"] == "creation" else service.editing
    review = supervisor.store.review(link["proposal_id"])
    provenance = (
        review.get("provenance")
        if link["service"] == "creation"
        else review["plan"]["provenance"]
    )
    return supervisor, review, publication_review(provenance)


def approve_release(service, link):
    supervisor, review, release = review_link(service, link)
    supervisor.store.approve(
        link["proposal_id"],
        review["review_digest"],
        "Offline public reviewer",
        publication_digest=release["review_digest"],
    )
    return supervisor


def terminal_review(database, review, *, kind="creation", publish=True):
    """Exercise real CLI/PTY; test data only, with no token in the process."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("KANKA_")}
    provenance = (
        review["provenance"] if kind == "creation" else review["plan"]["provenance"]
    )
    release = publication_review(provenance)
    phrase = (
        (
            f"publish {review['proposal_id']} {review['review_digest']} "
            f"{release['review_digest']}"
        )
        if publish
        else f"approve {review['proposal_id']} {review['review_digest']}"
    )
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "loremaster.approve",
                "--database",
                str(database),
                "--kind",
                kind,
                "approve",
                review["proposal_id"],
            ],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            text=True,
        )
        os.write(master, (phrase + "\n").encode())
        stdout, stderr = process.communicate(timeout=10)
        assert (process.returncode == 0) is publish, stderr
        assert "PUBLIC RELEASE" in stdout
        assert SECRET in stdout
        return stdout
    finally:
        os.close(master)
        os.close(slave)


async def test_nonadmin_public_creations_posts_and_edits_reuse_reviewed_executors(
    bridge,
):
    service, api = bridge
    data = await public_preview(service, api)
    public_ids = {}
    for key in (
        "root",
        "child",
        "root-post",
        "existing-post",
        "journal",
        "post-edit",
        "inventory-edit",
        "age",
    ):
        link = await handoff(service, data, key)
        supervisor, review, release = review_link(service, link)
        assert SECRET in release["original_source"]
        assert SECRET not in json.dumps(release["concrete_draft"])
        assert release["redaction_diff"]
        with pytest.raises(PermissionError, match="terminal release"):
            supervisor.store.approve(
                link["proposal_id"], review["review_digest"], "Reviewer"
            )
        with pytest.raises(KankaError):
            await supervisor.execute(7, link["proposal_id"])
        assert supervisor.store.review(link["proposal_id"])["status"] == "pending"
        approve_release(service, link)
        result = await supervisor.execute(7, link["proposal_id"])
        assert result["status"] == "verified", result
        refreshed = await service.refresh(7, data["import_id"], key)
        assert refreshed["status"] == "complete"
        public_ids[key] = refreshed["verified_result"]
    assert public_ids["root-post"]["entity_id"] == public_ids["root"]["entity_id"]
    assert api.records[101]["age"] == "45"
    assert api.attachments["inventory"][0]["amount"] == "5"
    for uid, record in api.records.items():
        if uid in {public_ids[k]["entity_id"] for k in ("root", "child", "journal")}:
            assert record["is_private"] is False
            assert SECRET not in record["entry"]
            assert "Source session:" not in record["entry"]
    sent = [body for method, path, body in api.requests if method in ("POST", "PATCH")]
    assert len(sent) == 8
    assert SECRET not in json.dumps(sent)
    assert "publication" not in json.dumps(sent)


@pytest.mark.parametrize("kind,key", [("creation", "root"), ("edit", "age")])
async def test_terminal_requires_publish_phrase_for_both_supervisors(
    bridge, tmp_path, kind, key
):
    service, api = bridge
    data = await public_preview(service, api, public_plan({key}))
    link = await handoff(service, data, key)
    supervisor, review, release = review_link(service, link)
    terminal_review(tmp_path / "supervised.db", review, kind=kind, publish=False)
    assert supervisor.store.review(link["proposal_id"])["status"] == "pending"
    with pytest.raises(PermissionError):
        supervisor.store.approve(
            link["proposal_id"],
            review["review_digest"],
            "Reviewer",
            publication_digest="0" * 64,
        )
    terminal_review(tmp_path / "supervised.db", review, kind=kind)
    assert (await supervisor.execute(7, link["proposal_id"]))["status"] == "verified"


@pytest.mark.parametrize(
    "failure",
    [
        "no_review",
        "private_full_session",
        "private_auto_body",
        "private_reference",
        "private_dependency",
        "private_destination",
    ],
)
async def test_public_preview_blocks_unsafe_release_paths(bridge, failure):
    service, api = bridge
    proposed = public_plan(
        {"root", "child"} if failure == "private_dependency" else {"root"}
    ).model_dump(mode="json")
    root = proposed["operations"][0]
    if failure == "no_review":
        root["publication"] = None
    elif failure == "private_full_session":
        root.update(entity_type="journal", session_text=True)
    elif failure == "private_auto_body":
        root["body"] = []
    elif failure in ("private_reference", "private_destination"):
        if failure == "private_reference":
            root["references"] = [
                {
                    "field": "parent_entity_id",
                    "target": {"kind": "mention", "key": "quest"},
                }
            ]
            root["entity_type"] = "quest"
        else:
            root.update(
                action="edit_entity",
                entity_type="quest",
                target={"kind": "mention", "key": "quest"},
            )
            root["fields"] = {}
    elif failure == "private_dependency":
        root.update(audience="admin", publication=None)
    # Set privacy after public_preview's normal fixture setup but before analysis.
    if failure in ("private_reference", "private_destination"):
        original_import = service.notes.import_text

        def private_owner(*args, **kwargs):
            api.records[602]["is_private"] = True
            return original_import(*args, **kwargs)

        service.notes.import_text = private_owner
    data = await public_preview(service, api, NotePlan.model_validate(proposed))
    rows = service.notes.inspect(7, data["import_id"], "preview")["data"]
    key = "child" if failure == "private_dependency" else "root"
    op = next(row for row in rows if row.get("key") == key)
    assert op["status"] == "blocked"
    assert op["blockers"]
    with pytest.raises(ValueError, match="blocked"):
        await handoff(service, data, key)
    assert all(method == "GET" for method, path, body in api.requests)


@pytest.mark.parametrize(
    "section,public",
    [("core", True), ("posts", True), ("posts", False), ("inventory", False)],
)
async def test_public_canon_citation_is_limited_to_public_projection(
    bridge, section, public
):
    service, api = bridge
    # The source fixture has public core text and one public/one creator post.
    proposed = public_plan({"root"}).model_dump(mode="json")
    # Obtain the exact stored evidence after analysis, then prepare a plan.
    data = await public_preview(service, api, public_plan({"root"}))
    row = service.notes._load(7, data["import_id"])
    records = row["state"]["evidence"]
    if section == "core":
        evidence = next(
            e
            for e in records.values()
            if e["entity_id"] == 101
            and e["section"] == "core"
            and "Old lore" in e["text"]
        )
        excerpt = "Old lore."
    elif section == "posts":
        name = "First post" if public else "Second post"
        evidence = next(
            e for e in records.values() if e["section"] == "posts" and name in e["text"]
        )
        excerpt = name
    else:
        evidence = next(e for e in records.values() if e["section"] == "inventory")
        excerpt = "amount"
    proposed["assessments"] = [
        {
            "claim_key": "goal",
            "state": "source_backed_assertion",
            "explanation": "Compare this retrieved wording during review.",
            "citations": [{"evidence_id": evidence["key"], "excerpt": excerpt}],
        }
    ]
    data = await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(proposed),
    )
    op = next(
        r
        for r in service.notes.inspect(7, data["import_id"], "preview")["data"]
        if r.get("key") == "root"
    )
    assert (op["status"] == "ready_for_review") is public
    if public:
        link = await handoff(service, data, "root")
        supervisor = approve_release(service, link)
        assert (await supervisor.execute(7, link["proposal_id"]))[
            "status"
        ] == "verified"
    else:
        assert any("citation" in b for b in op["blockers"])


@pytest.mark.parametrize("change", ["privacy", "text"])
async def test_canon_changes_after_publication_approval_stop_before_post(
    bridge, change
):
    service, api = bridge
    data = await public_preview(service, api, public_plan({"root"}))
    row = service.notes._load(7, data["import_id"])
    evidence = next(
        e
        for e in row["state"]["evidence"].values()
        if e["section"] == "core" and e["entity_id"] == 101 and "Old lore" in e["text"]
    )
    proposed = public_plan({"root"}).model_dump(mode="json")
    proposed["assessments"] = [
        {
            "claim_key": "goal",
            "state": "source_backed_assertion",
            "explanation": "Review source",
            "citations": [{"evidence_id": evidence["key"], "excerpt": "Old lore."}],
        }
    ]
    data = await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(proposed),
    )
    link = await handoff(service, data, "root")
    supervisor = approve_release(service, link)
    api.records[101]["is_private" if change == "privacy" else "entry"] = (
        True if change == "privacy" else "Changed text"
    )
    result = await supervisor.execute(7, link["proposal_id"])
    assert result["status"] == "failed"
    assert result["error"]["code"] == "publication_source_changed"
    assert supervisor.store.journal.states(link["proposal_id"]) == {}
    assert all(method == "GET" for method, path, body in api.requests)


async def test_public_full_journals_preserve_dates_and_quotes_for_public_source(notes):
    service, api = notes
    service.client._admin_campaign_ids = frozenset()
    api.is_private = False
    # Use the existing two-session source, explicitly classified for public review.
    from note_fixture import TEXT as source
    from note_fixture import analysis as analyse

    from loremaster.note_models import NoteAnalysis

    imported = service.import_text(
        7, source, ImportContext(locale="dmy", audience="all")
    )
    data = await service.submit(
        7, imported["import_id"], 0, NoteAnalysis.model_validate(analyse())
    )
    proposed = editorial_plan()
    proposed["operations"] = [
        op for op in proposed["operations"] if op["key"].startswith("journal")
    ]
    for op in proposed["operations"]:
        op.update(
            audience="all",
            publication={"rationale": "Review the public full-session copy."},
        )
    data = await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(proposed),
    )
    ops = [
        op
        for op in service.inspect(7, data["import_id"], "preview")["data"]
        if op["kind"] == "operation"
    ]
    assert all(op["status"] == "ready_for_review" for op in ops)
    assert {op["payload"]["date"] for op in ops} == {"2026-10-17", "2026-10-19"}
    assert all(op["payload"]["is_private"] is False for op in ops)
    assert "«no pienso bolver, ni por mil monedas»" in ops[0]["payload"]["entry"]
    assert "llegó" in ops[0]["payload"]["entry"]


@pytest.mark.parametrize(
    "publication",
    [
        {"rationale": "Review", "approved": True},
        {"rationale": "Review", "review_digest": "0" * 64},
        True,
    ],
)
def test_model_cannot_submit_publication_approval_or_digest(publication):
    proposed = public_plan({"root"}).model_dump(mode="json")
    proposed["operations"][0]["publication"] = publication
    with pytest.raises(ValidationError):
        NotePlan.model_validate(proposed)


async def test_grouped_public_posts_bind_both_copies_and_release_reasons(bridge):
    service, api = bridge
    proposed = public_plan({"existing-post"}).model_dump(mode="json")
    second = dict(
        proposed["operations"][0],
        key="second-scene",
        publication={"rationale": "Include the separately selected second scene."},
        body=[
            {"kind": "text", "text": "The second scene covered crossing the bridge."}
        ],
    )
    proposed["operations"].append(second)
    data = await public_preview(service, api, NotePlan.model_validate(proposed))
    rows = service.notes.inspect(7, data["import_id"], "preview")["data"]
    posts = [r for r in rows if r["kind"] == "operation"]
    assert len(posts) == 1
    assert posts[0]["source_operations"] == ["existing-post", "second-scene"]
    assert len(posts[0]["publication"]["reasons"]) == 2
    assert posts[0]["payload"]["entry"].count("second scene") == 1
    link = await handoff(service, data, "existing-post")
    supervisor = approve_release(service, link)
    assert (await supervisor.execute(7, link["proposal_id"]))["status"] == "verified"


async def test_publication_does_not_bypass_an_uncertain_private_same_name(bridge):
    service, api = bridge
    from test_note_bridge import preview

    private = await preview(service)
    link = await handoff(service, private, "root")
    service.creation.store.approve(
        link["proposal_id"], link["review_digest"], "Offline admin"
    )
    api.creation_failure = 500
    result = await service.creation.execute(7, link["proposal_id"])
    assert result["status"] == "uncertain"
    data = await public_preview(service, api, public_plan({"root"}))
    with pytest.raises(PermissionError, match="uncertain creation"):
        await handoff(service, data, "root")
    assert service.creation.status(7, link["proposal_id"])["steps"] == {
        "note": "uncertain"
    }
    assert sum(method == "POST" for method, path, body in api.requests) == 1


async def test_changed_public_copy_cannot_reuse_an_existing_handoff(bridge):
    service, api = bridge
    proposed = public_plan({"root"})
    data = await public_preview(service, api, proposed)
    first = await handoff(service, data, "root")
    _, review, release = review_link(service, first)
    changed = proposed.model_dump(mode="json")
    changed["operations"][0]["body"][0]["text"] = (
        "An explicitly different public version."
    )
    data = await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(changed),
    )
    with pytest.raises(ValueError, match="different handoff|already bound"):
        await handoff(service, data, "root")
    current = service.creation.store.review(first["proposal_id"])
    assert current["status"] == "pending"
    assert current["review_digest"] == review["review_digest"]
    assert (
        current["provenance"]["publication"]["review_digest"]
        == release["review_digest"]
    )


async def test_real_nonadmin_stdio_handoff_and_publication_cli(tmp_path):
    authority, notes_path = tmp_path / "authority.db", tmp_path / "notes.db"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests/lore_stdio_server.py")],
        cwd=str(ROOT),
        env={
            "KANKA_API_TOKEN": "offline-public-notes-token",
            "KANKA_ALLOWED_CAMPAIGN_IDS": "7",
            "KANKA_CREATION_DATABASE": str(authority),
            "KANKA_WRITE_CAMPAIGN_IDS": "7",
            "KANKA_WRITE_OPERATIONS": "note.create,journal.create,post.create",
            "KANKA_NOTES_DATABASE": str(notes_path),
            "KANKA_NOTES_HANDOFF": "prepare",
        },
    )
    async with asyncio.timeout(45):
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 27

                async def call(tool, **kwargs):
                    result = await session.call_tool(tool, {"campaign_id": 7, **kwargs})
                    assert not result.isError, result
                    return result.structuredContent["data"]

                source = TEXT.replace(
                    "Recover the blue key.", "Recover the blue key. " + SECRET
                )
                imported = await call(
                    "import_session_notes", text=source, context={"locale": "dmy"}
                )
                iid = imported["import_id"]
                analysed = await call(
                    "submit_session_note_analysis",
                    import_id=iid,
                    expected_version=0,
                    analysis=analysis().model_dump(mode="json"),
                )
                while analysed["work_cursor"]:
                    analysed = await call(
                        "submit_session_note_analysis",
                        import_id=iid,
                        expected_version=analysed["version"],
                        continuation=analysed["work_cursor"],
                    )
                planned = await call(
                    "prepare_session_note_proposals",
                    import_id=iid,
                    expected_version=analysed["version"],
                    evidence_digest=analysed["evidence_digest"],
                    plan=public_plan(
                        {"root", "child", "root-post", "journal"}
                    ).model_dump(mode="json"),
                )
                created = {}
                for key in ("root", "child", "root-post", "journal"):
                    link = await call(
                        "prepare_session_note_handoff",
                        import_id=iid,
                        expected_version=planned["version"],
                        preview_digest=planned["preview_digest"],
                        operation_key=key,
                    )
                    store = LocalApprovals(authority)
                    try:
                        review = store.review(link["proposal_id"])
                        assert review["status"] == "pending"
                        assert (
                            review["provenance"]["publication"]["source_audience"]
                            == "admin"
                        )
                    finally:
                        store.close()
                    denied = await session.call_tool(
                        "execute_creation",
                        {"campaign_id": 7, "proposal_id": link["proposal_id"]},
                    )
                    assert denied.isError
                    terminal_review(authority, review)
                    outcome = await call(
                        "execute_creation", proposal_id=link["proposal_id"]
                    )
                    assert outcome["status"] == "verified"
                    refreshed = await call(
                        "inspect_session_note_handoff",
                        import_id=iid,
                        operation_key=key,
                        verify_result=True,
                    )
                    created[key] = refreshed["verified_result"]
                assert created["root-post"]["entity_id"] == created["root"]["entity_id"]
                core = await call("get_entity", entity_id=created["root"]["entity_id"])
                assert core["details"]["is_private"] is False
                assert SECRET not in core["details"]["entry"]
                posts = await call(
                    "get_entity_posts", entity_id=created["root"]["entity_id"]
                )
                assert len(posts) == 1 and posts[0]["visibility_id"] == 1
    assert b"offline-public-notes-token" not in authority.read_bytes()
    assert b"offline-public-notes-token" not in notes_path.read_bytes()


async def test_explicit_public_quote_keeps_source_spelling_and_omits_private_context(
    bridge, monkeypatch
):
    service, api = bridge
    original_import = service.notes.import_text
    quote = "«no pienso bolver, ni por mil monedas»"
    speech = "Duncan: " + quote

    def with_dialogue(campaign_id, text, context, **kwargs):
        return original_import(
            campaign_id,
            text + speech + "\n",
            context,
            **kwargs,
        )

    monkeypatch.setattr(service.notes, "import_text", with_dialogue)
    original_analysis = analysis

    def with_speech():
        analysed = original_analysis().model_dump(mode="json")
        analysed["sessions"][0]["block_ids"].append("b-5")
        analysed["analysed_blocks"].append("b-5")
        return NoteAnalysis.model_validate(analysed)

    monkeypatch.setattr(sys.modules[__name__], "analysis", with_speech)
    proposed = public_plan({"root"}).model_dump(mode="json")
    proposed["operations"][0]["body"] = [
        {"kind": "quote", "anchor": {"block_id": "b-5", "excerpt": speech}}
    ]
    data = await public_preview(service, api, NotePlan.model_validate(proposed))
    link = await handoff(service, data, "root")
    supervisor = approve_release(service, link)
    result = await supervisor.execute(7, link["proposal_id"])
    assert result["status"] == "verified"
    entry = api.records[result["entity_id"]]["entry"]
    assert quote in entry
    assert "Duncan:" in entry
    assert SECRET not in entry
    assert "volver" not in entry
    assert "Source session" not in entry


async def test_public_quote_never_expands_into_unselected_private_context(
    bridge, monkeypatch
):
    service, api = bridge
    original_import = service.notes.import_text
    quote = "«no pienso bolver»"

    def with_context(campaign_id, text, context, **kwargs):
        return original_import(
            campaign_id,
            text.replace(SECRET, SECRET + " Duncan dijo " + quote),
            context,
            **kwargs,
        )

    monkeypatch.setattr(service.notes, "import_text", with_context)
    proposed = public_plan({"root"}).model_dump(mode="json")
    proposed["operations"][0]["body"] = [
        {"kind": "quote", "anchor": {"block_id": "b-4", "excerpt": quote}}
    ]
    with pytest.raises(ValueError, match="complete protected"):
        await public_preview(service, api, NotePlan.model_validate(proposed))
    assert all(method == "GET" for method, path, body in api.requests)


async def test_public_citations_cannot_release_private_fields_of_public_core(
    bridge, monkeypatch
):
    service, api = bridge
    original_import = service.notes.import_text

    def private_field(*args, **kwargs):
        api.records[101]["private_annotation"] = SECRET
        return original_import(*args, **kwargs)

    monkeypatch.setattr(service.notes, "import_text", private_field)
    data = await public_preview(service, api, public_plan({"root"}))
    row = service.notes._load(7, data["import_id"])
    evidence = next(
        e
        for e in row["state"]["evidence"].values()
        if e["section"] == "core" and SECRET in e["text"]
    )
    proposed = public_plan({"root"}).model_dump(mode="json")
    proposed["assessments"] = [
        {
            "claim_key": "goal",
            "state": "source_backed_assertion",
            "explanation": "Unclassified private field",
            "citations": [{"evidence_id": evidence["key"], "excerpt": SECRET}],
        }
    ]
    data = await service.notes.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(proposed),
    )
    op = next(
        op
        for op in service.notes.inspect(7, data["import_id"], "preview")["data"]
        if op.get("key") == "root"
    )
    assert op["status"] == "blocked"
    assert any("private or unclassified evidence" in b for b in op["blockers"])
    assert all(method == "GET" for method, path, body in api.requests)
