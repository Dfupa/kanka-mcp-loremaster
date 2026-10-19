"""Offline organisation/member contract; fixture IDs never identify live records."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from supervised_fixture import handle
from test_supervised_execution import authorize
from test_supervised_execution import service as service

from loremaster.approvals import LocalApprovals
from loremaster.creation import (
    CreationScope,
    OrganisationDraft,
    OrganisationMemberDraft,
    preview_creation,
    resolve_creation_request,
)
from loremaster.execution import CreationService
from loremaster.kanka import KankaError
from loremaster.server import create_server


@pytest.fixture
def organisation(service):
    creator, api = service
    creator.scope = CreationScope(
        campaign_ids=frozenset({7}),
        operations=creator.scope.operations
        | {"organisation.create", "organisation.member.add"},
    )
    api.records[301] = {
        "id": 31,
        "entity_id": 301,
        "name": "Synthetic Éldan",
        "entry": "Synthetic character",
    }
    return OrganisationDraft(
        campaign_id=7,
        name="Synthetic organisation",
        entry="<p>Offline test organisation.</p>",
        is_private=False,
        type="Guild",
        members=(
            OrganisationMemberDraft(character_entity_id=301, is_private=False),
            OrganisationMemberDraft(character_entity_id=101, is_private=False),
        ),
    )


def test_member_templates_resolve_module_ids_not_universal_ids():
    draft = OrganisationDraft(
        campaign_id=7,
        name="Org",
        entry="Description",
        members=(OrganisationMemberDraft(character_entity_id=301),),
    )
    preview = preview_creation(draft)
    assert [row["operation"] for row in preview["operations"]] == [
        "organisation.create",
        "organisation.member.add",
    ]
    assert preview["operations"][0]["body"]["is_private"] is True
    assert preview["operations"][1]["body"]["is_private"] is True
    with pytest.raises(ValueError, match="organisation module ID"):
        resolve_creation_request(draft, "member:0", entity_id=999, module_ids={301: 31})
    path, body = resolve_creation_request(
        draft, "member:0", entity_id=999, child_id=99, module_ids={301: 31}
    )
    assert path == "campaigns/7/organisations/99/organisation_members"
    assert body == {
        "organisation_id": 99,
        "character_id": 31,
        "role": "Member",
        "is_private": True,
        "pin_id": 0,
        "status_id": 0,
    }


@pytest.mark.parametrize(
    "values",
    [
        {"character_entity_id": True},
        {"pin_id": True},
        {"pin_id": 4},
        {"status_id": 3},
        {"is_private": "false"},
        {"role": " "},
    ],
)
def test_invalid_membership_fields_fail_validation(values):
    with pytest.raises(ValidationError):
        OrganisationMemberDraft.model_validate({"character_entity_id": 301, **values})


def test_duplicate_character_members_are_rejected():
    member = OrganisationMemberDraft(character_entity_id=301)
    with pytest.raises(ValidationError, match="distinct characters"):
        OrganisationDraft(
            campaign_id=7, name="Org", entry="Description", members=(member, member)
        )


async def test_mcp_organisation_creation_has_two_real_memberships(
    service, organisation
):
    creator, api = service
    server = create_server(creator.reader, creator)
    prepared = await server.call_tool(
        "prepare_organisation_creation", organisation.model_dump(mode="json")
    )
    assert not prepared.isError
    proposal = prepared.structuredContent["data"]
    assert all(method == "GET" for method, _path in api.requests)
    assert creator.store.draft(proposal["proposal_id"]) == organisation
    args = {"campaign_id": 7, "proposal_id": proposal["proposal_id"]}
    wrong = await server.call_tool("execute_character_creation", args)
    assert (
        wrong.isError
        and creator.store.review(proposal["proposal_id"])["status"] == "pending"
    )
    denied = await server.call_tool("execute_creation", args)
    assert (
        denied.isError
        and denied.structuredContent["error"]["code"] == "approval_required"
    )
    authorize(creator, proposal)
    created = await server.call_tool("execute_creation", args)
    assert not created.isError
    result = created.structuredContent["data"]
    assert result["status"] == "verified" and result["entity_id"] != result["child_id"]
    rows = api.attachments[result["entity_id"]]["members"]
    assert {row["character_id"] for row in rows} == {31, 11}
    assert all(
        row["organisation_id"] == result["child_id"]
        and row["is_private"] is False
        and row["role"] == "Member"
        and row["status_id"] == 0
        for row in rows
    )
    assert len([method for method, _path in api.requests if method == "POST"]) == 3
    assert set(creator.status(7, proposal["proposal_id"])["steps"].values()) == {
        "confirmed"
    }


@pytest.mark.parametrize(
    "permission", ["organisation.create", "organisation.member.add"]
)
async def test_each_organisation_operation_requires_scope(
    service, organisation, permission
):
    creator, api = service
    creator.scope = CreationScope(
        campaign_ids=frozenset({7}), operations=creator.scope.operations - {permission}
    )
    before = list(api.requests)
    with pytest.raises(PermissionError, match="scope"):
        await creator.prepare(organisation)
    assert api.requests == before


@pytest.mark.parametrize("change", ["wrong_type", "missing", "renamed"])
async def test_members_must_resolve_to_reviewed_characters(
    service, organisation, change
):
    creator, api = service
    if change == "wrong_type":
        draft = organisation.model_copy(
            update={"members": (OrganisationMemberDraft(character_entity_id=201),)}
        )
        with pytest.raises(ValueError, match="character"):
            await creator.prepare(draft)
    elif change == "missing":
        api.records.pop(301)
        with pytest.raises(KankaError):
            await creator.prepare(organisation)
    else:
        proposal = await creator.prepare(organisation)
        authorize(creator, proposal)
        api.records[301]["name"] = "Changed identity"
        assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "failed"
    assert not any(method == "POST" for method, _path in api.requests)


async def test_rejected_membership_resumes_after_restart_without_duplicates(
    service, organisation, tmp_path
):
    creator, api = service
    proposal = await creator.prepare(organisation)
    authorize(creator, proposal)
    api.failure = ("member:1", 403)
    first = await creator.execute(7, proposal["proposal_id"])
    assert first["status"] == "partial" and first["completed"] == [
        "organisation",
        "member:0",
    ]
    assert first["failed_step"] == "member:1"
    assert "SYNTHETIC PRIVATE FAILURE" not in json.dumps(first)
    await creator.aclose()
    resumed = CreationService(
        creator.reader,
        "offline-test-token",
        LocalApprovals(tmp_path / "private.sqlite3"),
        creator.scope,
        transport=httpx.MockTransport(lambda request: handle(api, request)),
    )
    try:
        assert isinstance(
            resumed.store.draft(proposal["proposal_id"]), OrganisationDraft
        )
        denied = await create_server(resumed.reader, resumed).call_tool(
            "execute_creation",
            {"campaign_id": 7, "proposal_id": proposal["proposal_id"]},
        )
        assert denied.isError
        api.failure = None
        authorize(resumed, proposal)
        result = await resumed.execute(7, proposal["proposal_id"])
        assert result["status"] == "verified"
        assert len(api.attachments[result["entity_id"]]["members"]) == 2
        assert sum(method == "POST" for method, _path in api.requests) == 4
    finally:
        await resumed.aclose()


@pytest.mark.parametrize("step", ["organisation", "member:0", "member:1"])
async def test_uncertain_organisation_steps_require_explicit_reconciliation(
    service, organisation, step
):
    creator, api = service
    proposal = await creator.prepare(organisation)
    authorize(creator, proposal)
    api.lose_response = step
    first = await creator.execute(7, proposal["proposal_id"])
    assert first["status"] == "uncertain" and first["failed_step"] == step
    with pytest.raises(PermissionError, match="Reconcile uncertain"):
        authorize(creator, proposal)
    uid = next(
        uid for uid, row in api.records.items() if row["name"] == organisation.name
    )
    candidate = (
        uid
        if step == "organisation"
        else api.attachments[uid]["members"][int(step.split(":")[1])]["id"]
    )
    before = len(api.requests)
    evidence = await creator.inspect_reconciliation(
        7, proposal["proposal_id"], candidate
    )
    assert all(method == "GET" for method, _path in api.requests[before:])
    assert creator.store.journal.progress(proposal["proposal_id"])["uncertain"] == step
    creator.store.journal.confirm_reconciled(
        proposal["proposal_id"],
        evidence["fingerprint"],
        evidence["record"],
        reviewed_by="Offline reviewer",
    )
    assert creator.store.review(proposal["proposal_id"])["status"] == "claimed"
    api.lose_response = None
    authorize(creator, proposal)
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert sum(method == "POST" for method, _path in api.requests) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "missing_pagination",
        "owner",
        "campaign",
        "duplicate_id",
        "privacy",
        "character",
        "replaced",
    ],
)
async def test_invalid_membership_readback_never_reports_success(
    service, organisation, fault
):
    creator, api = service
    proposal = await creator.prepare(organisation)
    authorize(creator, proposal)

    def handler(request):
        response = handle(api, request)
        if request.method == "GET" and request.url.path.endswith(
            "/organisation_members"
        ):
            payload = response.json()
            if fault == "missing_pagination":
                payload.pop("meta")
            elif fault == "duplicate_id":
                payload["data"][1]["id"] = payload["data"][0]["id"]
            else:
                key = {
                    "owner": "organisation_id",
                    "campaign": "campaign_id",
                    "privacy": "is_private",
                    "character": "character_id",
                    "replaced": "id",
                }[fault]
                payload["data"][0][key] = True if fault == "privacy" else 999
            return httpx.Response(200, json=payload)
        return response

    await creator.reader._http.aclose()
    creator.reader._http = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(handler)
    )
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "partial" and result["failed_step"] == "verification"
    assert creator.store.review(proposal["proposal_id"])["status"] != "complete"


async def test_member_verification_reads_all_pages(service, organisation):
    creator, api = service
    proposal = await creator.prepare(organisation)
    authorize(creator, proposal)

    def handler(request):
        response = handle(api, request)
        if request.method == "GET" and request.url.path.endswith(
            "/organisation_members"
        ):
            payload = response.json()
            page = int(request.url.params["page"])
            payload["data"] = payload["data"][page - 1 : page]
            payload["meta"] = {"current_page": page, "last_page": 2, "per_page": 1}
            return httpx.Response(200, json=payload)
        return response

    await creator.reader._http.aclose()
    creator.reader._http = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(handler)
    )
    assert (await creator.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert (
        sum(
            method == "GET" and path.endswith("organisation_members")
            for method, path in api.requests
        )
        == 2
    )


async def test_cancellation_during_membership_send_preserves_uncertainty(
    service, organisation
):
    creator, api = service
    proposal = await creator.prepare(organisation)
    authorize(creator, proposal)
    applied = asyncio.Event()

    async def handler(request):
        response = handle(api, request)
        if api._active_key == "member:0":
            applied.set()
            await asyncio.Event().wait()
        return response

    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(handler)
    )
    task = asyncio.create_task(creator.execute(7, proposal["proposal_id"]))
    async with asyncio.timeout(3):
        await applied.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (
        creator.store.journal.progress(proposal["proposal_id"])["uncertain"]
        == "member:0"
    )
