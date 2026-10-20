"""Non-admin defaults must fail before writes without changing confidentiality."""

import httpx
import pytest
from note_fixture import plan
from test_editing import approve as approve_edit
from test_editing import draft as edit_draft
from test_editing import editor as editor
from test_session_notes import notes as notes
from test_session_notes import prepared
from test_supervised_execution import authorize
from test_supervised_execution import service as service

from loremaster.creation import (
    CharacterDraft,
    CreationScope,
    EntityPostDraft,
    OrganisationDraft,
    OrganisationMemberDraft,
)
from loremaster.kanka import KankaClient, KankaError
from loremaster.note_models import NotePlan
from loremaster.server import create_server, main


@pytest.mark.parametrize("visibility", [1, 2, 3, 4, 5])
async def test_non_admin_visibility_codes_include_creator_and_members(visibility):
    client = KankaClient(
        "offline",
        transport=httpx.MockTransport(lambda r: pytest.fail("No HTTP expected")),
    )
    try:
        if visibility == 2:
            with pytest.raises(KankaError) as error:
                client.require_write_visibility(7, visibility_id=visibility)
            assert error.value.code == "visibility_requires_admin"
        else:
            assert client.require_write_visibility(7, visibility_id=visibility) is None
        with pytest.raises(KankaError, match="non-admin"):
            client.require_write_visibility(7, is_private=True)
    finally:
        await client.aclose()


async def test_explicit_admin_declaration_is_campaign_scoped():
    client = KankaClient(
        "offline",
        allowed_campaign_ids=frozenset({7, 8}),
        admin_campaign_ids=frozenset({7}),
    )
    try:
        assert (
            client.require_write_visibility(7, is_private=True, visibility_id=2) is None
        )
        with pytest.raises(KankaError):
            client.require_write_visibility(8, is_private=True)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "admin_ids",
    [
        None,
        frozenset({True}),
        frozenset({0}),
        frozenset({-1}),
        frozenset({"7"}),
        frozenset({8}),
    ],
)
def test_admin_scope_rejects_malformed_or_disallowed_ids(admin_ids):
    with pytest.raises(ValueError):
        KankaClient(
            "offline", allowed_campaign_ids=frozenset({7}), admin_campaign_ids=admin_ids
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"is_private": 1},
        {"visibility_id": True},
        {"visibility_id": 0},
        {"visibility_id": 6},
    ],
)
async def test_visibility_validation_is_a_runtime_guard(arguments):
    client = KankaClient("offline")
    try:
        with pytest.raises(ValueError):
            client.require_write_visibility(7, **arguments)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "kind", ["private_core", "admin_post", "nested_admin_post", "private_member"]
)
async def test_non_admin_creation_rejects_before_any_request_or_proposal(service, kind):
    creator, api = service
    creator.reader._admin_campaign_ids = frozenset()
    if kind == "private_member":
        creator.scope = CreationScope(
            campaign_ids=frozenset({7}),
            operations=frozenset({"organisation.create", "organisation.member.add"}),
        )
        draft = OrganisationDraft(
            campaign_id=7,
            name="Public organisation",
            entry="Public",
            is_private=False,
            members=(OrganisationMemberDraft(character_entity_id=101),),
        )
    elif kind == "admin_post":
        draft = EntityPostDraft(
            campaign_id=7,
            entity_id=101,
            name="Private post",
            entry="Secret",
            visibility_id=2,
        )
    else:
        draft = CharacterDraft(
            campaign_id=7,
            name="Diagnostic",
            entry="Secret",
            is_private=kind == "private_core",
            posts=({"name": "Private post", "entry": "Secret", "visibility_id": 2},)
            if kind == "nested_admin_post"
            else (),
        )
    before = len(api.requests)
    with pytest.raises(KankaError) as error:
        await creator.prepare(draft)
    assert error.value.code == "visibility_requires_admin"
    assert len(api.requests) == before
    assert (
        creator.store._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
    )


async def test_public_creation_and_creator_readable_post_work_without_admin(service):
    creator, api = service
    creator.reader._admin_campaign_ids = frozenset()
    proposal = await creator.prepare(
        CharacterDraft(
            campaign_id=7,
            name="Public diagnostic",
            entry="Reviewed public text",
            is_private=False,
            posts=(
                {
                    "name": "Creator readable",
                    "entry": "Own account",
                    "visibility_id": 3,
                },
            ),
        )
    )
    authorize(creator, proposal)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "verified"
    assert api.records[result["entity_id"]]["is_private"] is False
    assert sum(method == "POST" for method, path in api.requests) == 2


async def test_role_change_blocks_an_already_approved_private_creation(service):
    creator, api = service
    proposal = await creator.prepare(
        CharacterDraft(campaign_id=7, name="Private diagnostic", entry="Secret")
    )
    authorize(creator, proposal)
    creator.reader._admin_campaign_ids = frozenset()
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "failed"
    assert result["error"]["code"] == "visibility_requires_admin"
    assert creator.store.journal.states(proposal["proposal_id"]) == {}
    assert all(method == "GET" for method, path in api.requests)


async def test_explicit_admin_declaration_never_bypasses_kanka_denial(service):
    creator, api = service
    proposal = await creator.prepare(
        CharacterDraft(campaign_id=7, name="Private diagnostic", entry="Secret")
    )
    authorize(creator, proposal)
    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/",
        transport=httpx.MockTransport(lambda request: httpx.Response(403)),
    )
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "failed"
    assert result["error"]["status_code"] == 403
    assert (
        creator.store.journal.states(proposal["proposal_id"])["character"] == "rejected"
    )


@pytest.mark.parametrize("target", ["core", "post"])
async def test_private_edit_visibility_blocks_before_patch(editor, target):
    service, api = editor
    service.reader._admin_campaign_ids = frozenset()
    if target == "core":
        api.records[101]["is_private"] = True
        draft = edit_draft()
    else:
        api.attachments["posts"][0]["visibility_id"] = 2
        draft = edit_draft("post")
    with pytest.raises(KankaError) as error:
        await service.prepare(draft)
    assert error.value.code == "visibility_requires_admin"
    assert all(method == "GET" for method, path, body in api.requests)


async def test_edit_role_change_blocks_patch_before_intent(editor):
    service, api = editor
    api.records[101]["is_private"] = True
    proposal = await service.prepare(edit_draft())
    approve_edit(service, proposal)
    service.reader._admin_campaign_ids = frozenset()
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "failed"
    assert result["error"]["code"] == "visibility_requires_admin"
    assert result["outcome"] == "not_sent"
    assert all(method == "GET" for method, path, body in api.requests)


async def test_get_only_edit_recovery_survives_removal_of_admin_declaration(editor):
    service, api = editor
    api.records[101]["is_private"] = True
    proposal = await service.prepare(edit_draft())
    approve_edit(service, proposal)
    api.lose_response = True
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "uncertain"
    service.reader._admin_campaign_ids = frozenset()
    before = len(api.requests)
    evidence = await service.inspect_reconciliation(7, proposal["proposal_id"])
    assert evidence["matches_requested_result"] is True
    assert all(method == "GET" for method, path, body in api.requests[before:])
    assert service.status(7, proposal["proposal_id"])["outcome"] == "uncertain"


async def test_note_preview_explains_non_admin_block_without_publishing(notes):
    service, api = notes
    service.client._admin_campaign_ids = frozenset()
    data = await prepared(service)
    await service.prepare(
        7,
        data["import_id"],
        data["version"],
        data["evidence_digest"],
        NotePlan.model_validate(plan()),
    )
    rows = service.inspect(7, data["import_id"], "preview")["data"]
    journal = next(row for row in rows if row.get("key") == "journal12")
    assert journal["payload"]["is_private"] is True
    assert any("non-admin" in blocker for blocker in journal["blockers"])
    assert journal["status"] != "ready_for_review"
    assert all(request.method == "GET" for request in api.requests)


async def test_mcp_denial_exposes_role_error_and_no_role_override_schema(service):
    creator, api = service
    creator.reader._admin_campaign_ids = frozenset()
    server = create_server(creator.reader, creator)
    tools = await server.list_tools()
    schema = next(
        tool for tool in tools if tool.name == "prepare_character_creation"
    ).inputSchema
    assert "admin_campaign_ids" not in schema["properties"]
    response = await server.call_tool(
        "prepare_character_creation",
        {"campaign_id": 7, "name": "Private", "entry": "Secret"},
    )
    assert response.isError
    assert response.structuredContent["error"]["code"] == "visibility_requires_admin"
    assert api.requests == []


@pytest.mark.parametrize(
    "setting, expected", [(None, frozenset()), ("7", frozenset({7}))]
)
def test_startup_defaults_to_non_admin_and_requires_host_override(
    monkeypatch, setting, expected
):
    import loremaster.server as module

    for name in tuple(module.os.environ):
        if name.startswith("KANKA_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("KANKA_API_TOKEN", "offline")
    monkeypatch.setenv("KANKA_ALLOWED_CAMPAIGN_IDS", "7")
    if setting is not None:
        monkeypatch.setenv("KANKA_ADMIN_CAMPAIGN_IDS", setting)
    observed = {}

    def client(token, **kwargs):
        observed.update(kwargs)
        return object()

    class Server:
        def run(self, *, transport):
            assert transport == "stdio"

    monkeypatch.setattr(module, "KankaClient", client)
    monkeypatch.setattr(module, "create_server", lambda *args: Server())
    module.main()
    assert observed["admin_campaign_ids"] == expected


@pytest.mark.parametrize("setting", ["0", "-1", "bad", "7,", "8"])
def test_invalid_admin_startup_scope_fails_before_client_creation(monkeypatch, setting):
    monkeypatch.setenv("KANKA_API_TOKEN", "offline")
    monkeypatch.setenv("KANKA_ALLOWED_CAMPAIGN_IDS", "7")
    monkeypatch.setenv("KANKA_ADMIN_CAMPAIGN_IDS", setting)
    monkeypatch.setattr(
        "loremaster.server.KankaClient",
        lambda *args, **kwargs: pytest.fail("Startup must fail first"),
    )
    with pytest.raises(ValueError):
        main()
