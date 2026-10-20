"""Regressions from the live trial, with synthetic HTTP and no real credentials."""

import json
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest
from creation_fixtures import offline
from supervised_fixture import handle
from test_editing import approve as approve_edit
from test_editing import draft as edit_draft
from test_editing import editor as editor
from test_supervised_execution import authorize
from test_supervised_execution import service as service

from loremaster.approvals import LocalApprovals
from loremaster.kanka import KankaError
from loremaster.server import create_server
from loremaster.write_diagnostics import json_receipt, transport_failure


class InterruptedBody(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b'{"data":'
        raise httpx.ReadTimeout("Bearer PRIVATE_TOKEN must never escape")

    async def aclose(self):
        self.closed = True


async def replace_writer(creator, handler):
    await creator._writer.aclose()
    creator._writer = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(handler)
    )


async def test_received_201_body_timeout_is_durable_and_not_invalid_json(
    service, tmp_path
):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    stream = InterruptedBody()

    def interrupted(request):
        handle(api, request)
        return httpx.Response(
            201, headers={"content-type": "application/json"}, stream=stream
        )

    await replace_writer(creator, interrupted)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    assert result["error"]["status_code"] == 201
    receipt = result["diagnostics"][-1]["details"]
    assert receipt == {
        "stage": "response_body",
        "http_status": 201,
        "media_type": "application/json",
        "failure": "read_timeout",
    }
    assert stream.closed
    assert "PRIVATE_TOKEN" not in json.dumps(result)
    reopened = LocalApprovals(tmp_path / "private.sqlite3")
    try:
        assert (
            reopened.review(proposal["proposal_id"])["diagnostics"]
            == result["diagnostics"]
        )
        assert reopened.journal.states(proposal["proposal_id"]) == {
            "character": "uncertain"
        }
    finally:
        reopened.close()
    with pytest.raises(PermissionError, match="Reconcile uncertain"):
        authorize(creator, proposal)
    assert sum(method == "POST" for method, _path in api.requests) == 1


@pytest.mark.parametrize("fault", ["json", "envelope", "identity", "array"])
async def test_201_failure_diagnostics_do_not_copy_upstream_values(service, fault):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)

    def invalid(request):
        response = handle(api, request)
        if fault == "json":
            return httpx.Response(201, text="PRIVATE_TOKEN <html>private</html>")
        if fault == "envelope":
            return httpx.Response(201, json={"PRIVATE_TOKEN": "secret"})
        if fault == "array":
            return httpx.Response(201, json={"data": []})
        data = response.json()["data"]
        return httpx.Response(201, json={"data": {**data, "id": "PRIVATE_TOKEN"}})

    await replace_writer(creator, invalid)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    receipt = result["diagnostics"][-1]["details"]
    assert receipt["http_status"] == 201
    assert (
        receipt["failure"]
        == {
            "json": "invalid_json",
            "envelope": "invalid_envelope",
            "identity": "invalid_identity",
            "array": "invalid_identity",
        }[fault]
    )
    assert len(receipt["response_sha256"]) == 64
    assert "PRIVATE_TOKEN" not in json.dumps(result)
    if fault == "identity":
        assert receipt["identity_types"]["id"] == "string"
        assert "id" not in receipt["candidate_ids"]


@pytest.mark.parametrize("status", [200, 201])
async def test_valid_creation_response_statuses_are_both_verified(service, status):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)

    def success(request):
        return httpx.Response(status, json=handle(api, request).json())

    await replace_writer(creator, success)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "verified"
    assert all(
        row["details"]["http_status"] == status
        for row in creator.status(7, proposal["proposal_id"])["diagnostics"]
    )


async def test_slim_core_response_uses_only_verified_module_id_readback(service):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)

    def slim(request):
        payload = handle(api, request).json()
        if request.url.path.endswith("/characters"):
            payload = {"data": {"id": payload["data"]["id"]}}
        return httpx.Response(201, json=payload)

    await replace_writer(creator, slim)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "verified"
    receipt = creator.status(7, proposal["proposal_id"])["diagnostics"][0]["details"]
    assert receipt["stage"] == "identity_verified"
    assert receipt["candidate_ids"]["entity_id"] == result["entity_id"]
    assert sum(method == "POST" for method, _path in api.requests) == 5


async def test_http_500_is_recorded_without_reading_a_failing_error_body(service):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    stream = InterruptedBody()
    await replace_writer(creator, lambda _r: httpx.Response(500, stream=stream))
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    assert result["error"]["status_code"] == 500
    assert result["diagnostics"][-1]["details"]["failure"] == "http_error"
    assert stream.closed
    assert "PRIVATE_TOKEN" not in json.dumps(result)


async def test_receipt_candidate_recovery_is_get_only_and_requires_origin_review(
    service,
):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)

    def invalid_owner(request):
        payload = handle(api, request).json()
        payload["data"]["entity_id"] = "PRIVATE_TOKEN"
        return httpx.Response(201, json=payload)

    await replace_writer(creator, invalid_owner)
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    before = len(api.requests)
    response = await create_server(creator.reader, creator).call_tool(
        "get_creation_status",
        {
            "campaign_id": 7,
            "proposal_id": proposal["proposal_id"],
            "inspect_recovery": True,
        },
    )
    assert not response.isError
    recovery = response.structuredContent["data"]["recovery"]
    assert recovery["next_action"] == "review_origin_then_terminal_reconcile"
    assert recovery["candidates"][0]["fields_match"] is True
    assert recovery["origin_review_required"] is True
    assert recovery["absence_proves_rejection"] is False
    assert all(method == "GET" for method, _path in api.requests[before:])
    assert "uncertain" in creator.status(7, proposal["proposal_id"])["progress"]
    assert "PRIVATE_TOKEN" not in json.dumps(response.structuredContent)
    with pytest.raises(PermissionError):
        authorize(creator, proposal)


async def test_no_match_recovery_cannot_reset_or_replace_uncertainty(service):
    creator, _api = service
    draft = offline.example_draft()
    first = await creator.prepare(draft)
    second = await creator.prepare(draft)
    authorize(creator, first)
    authorize(creator, second)
    await replace_writer(creator, lambda _r: httpx.Response(500))
    assert (await creator.execute(7, first["proposal_id"]))["status"] == "uncertain"
    recovery = await creator.inspect_recovery(7, first["proposal_id"])
    assert recovery["recovery"]["search_complete"] is True
    assert recovery["recovery"]["candidates"] == []
    assert recovery["recovery"]["next_action"] == "review_upstream_outcome"
    with pytest.raises(PermissionError, match="new proposal"):
        await creator.prepare(draft)
    with pytest.raises(KankaError, match="new proposal"):
        await creator.execute(7, second["proposal_id"])


async def test_pacing_and_cooldown_survive_operation_contexts(service, monkeypatch):
    creator, api = service
    client = creator.reader
    sleep = AsyncMock()
    monkeypatch.setattr("loremaster.kanka.asyncio.sleep", sleep)
    async with client.account_reads():
        client.observe_response(
            httpx.Response(200, headers={"X-RateLimit-Limit": "30"})
        )
    sleep.reset_mock()
    await client._get("profile")
    assert 1.9 < sleep.await_args_list[0].args[0] <= 2
    client.observe_response(httpx.Response(429, headers={"Retry-After": "60"}))
    before = len(api.requests)
    with pytest.raises(KankaError) as error:
        await client._get("profile")
    assert error.value.status_code == 429
    assert 59 < error.value.retry_after_seconds <= 60
    assert len(api.requests) == before


async def test_nested_read_scopes_share_the_same_budget_and_profile(service):
    creator, api = service
    before = len(api.requests)
    async with creator.reader.account_reads():
        outer = creator.reader._budget.get()
        async with creator.reader.account_reads():
            assert creator.reader._budget.get() is outer
            await creator.reader.find_entities_by_name(7, "A missing entity")
        assert creator.reader._budget.get() is outer
    assert creator.reader._budget.get() is None
    assert sum(path == "profile" for _method, path in api.requests[before:]) == 1


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_write_json_diagnostics_reject_nonfinite_json(literal):
    response = httpx.Response(
        201, content='{"data":{"id":1,"entity_id":' + literal + "}}"
    )
    payload, receipt = json_receipt(response)
    assert payload is None and receipt["failure"] == "invalid_json"


def test_receipts_are_additive_for_a_legacy_proposal_database(tmp_path):
    draft = offline.example_draft()
    refs = {"campaign": {"id": 7, "name": "Synthetic"}, "entities": {}, "item_ids": {}}
    payload = {"draft": draft.model_dump(mode="json"), "references": refs}
    serialized = json.dumps(payload, ensure_ascii=False)
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE proposals(id TEXT PRIMARY KEY,payload TEXT,digest TEXT,"
            "state TEXT,approval_id TEXT,reviewer TEXT,deadline REAL)"
        )
        connection.execute(
            "INSERT INTO proposals VALUES(?,?,?,'pending',NULL,NULL,NULL)",
            ("legacy", serialized, LocalApprovals._digest(payload)),
        )
    path.chmod(0o600)
    store = LocalApprovals(path)
    try:
        review = store.review("legacy")
        assert review["diagnostics"] == [] and review["status"] == "pending"
        assert store.draft("legacy") == draft
        actual = store._db.execute(
            "SELECT payload FROM proposals WHERE id='legacy'"
        ).fetchone()[0]
        assert actual == serialized
    finally:
        store.close()


async def test_patch_body_timeout_after_201_keeps_owner_blocked_and_receipt(editor):
    service, api = editor
    proposal = await service.prepare(edit_draft())
    approve_edit(service, proposal)
    stream = InterruptedBody()

    def interrupted(request):
        api.handle(request)
        return httpx.Response(201, stream=stream)

    await replace_writer(service, interrupted)
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain" and stream.closed
    assert result["diagnostics"][-1]["details"]["failure"] == "read_timeout"
    assert result["diagnostics"][-1]["details"]["http_status"] == 201
    assert "PRIVATE_TOKEN" not in json.dumps(result)
    with pytest.raises(PermissionError):
        approve_edit(service, proposal)
    assert sum(method == "PATCH" for method, _path, _body in api.requests) == 1


async def test_patch_rejection_receipt_survives_fresh_approval_and_success(editor):
    service, api = editor
    proposal = await service.prepare(edit_draft())
    approve_edit(service, proposal)
    await replace_writer(
        service, lambda _r: httpx.Response(429, headers={"Retry-After": "60"})
    )
    rejected = await service.execute(7, proposal["proposal_id"])
    assert rejected["outcome"] == "rejected"
    assert rejected["error"]["retry_after_seconds"] == 60
    assert rejected["diagnostics"][-1]["details"]["retry_after_seconds"] == 60
    service.reader._cooldown_until = 0  # Simulate server cooldown expiry.
    await replace_writer(service, api.handle)
    approve_edit(service, proposal)
    complete = await service.execute(7, proposal["proposal_id"])
    assert complete["status"] == "verified"
    receipts = service.status(7, proposal["proposal_id"])["diagnostics"]
    assert [receipt["details"]["http_status"] for receipt in receipts] == [429, 200]


@pytest.mark.parametrize(
    "error_type,category",
    [
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.RemoteProtocolError, "remote_protocol_error"),
        (httpx.DecodingError, "body_decoding_error"),
        (httpx.ConnectError, "transport_error"),
    ],
)
def test_transport_categories_never_copy_exception_messages(error_type, category):
    assert transport_failure(error_type("PRIVATE_TOKEN")) == category


async def test_slim_response_cannot_cross_campaigns_during_module_resolution(service):
    creator, api = service
    proposal = await creator.prepare(offline.example_draft())
    authorize(creator, proposal)
    created = {}

    def slim(request):
        payload = handle(api, request).json()
        created["id"] = payload["data"]["id"]
        return httpx.Response(201, json={"data": {"id": created["id"]}})

    def crossed(request):
        response = handle(api, request)
        if created and (
            request.url.path == f"/1.0/campaigns/7/characters/{created['id']}"
        ):
            return httpx.Response(
                200, json={"data": {**response.json()["data"], "campaign_id": 8}}
            )
        return response

    await replace_writer(creator, slim)
    await creator.reader._http.aclose()
    creator.reader._http = httpx.AsyncClient(
        base_url="https://api.kanka.io/1.0/", transport=httpx.MockTransport(crossed)
    )
    result = await creator.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    assert result["diagnostics"][-1]["details"]["failure"] == "identity_readback_failed"
    assert sum(method == "POST" for method, _path in api.requests) == 1
