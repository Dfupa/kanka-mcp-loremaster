"""Inventory, relation and pagination contracts using synthetic campaign records."""

from collections.abc import Callable

import httpx
import pytest
from fake_kanka import entity, envelope, handler

from loremaster.kanka import KankaClient, KankaError


def client_for(
    override: Callable[[httpx.Request], httpx.Response] = handler,
) -> tuple[KankaClient, list[str]]:
    """Intercept all requests and retain paths to assert scoping and deduplication."""
    calls = []

    def record(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        calls.append(request.url.path)
        return override(request)

    return KankaClient(
        "offline-test-token", transport=httpx.MockTransport(record)
    ), calls


async def test_inventory_pages_resolve_real_item_ids_and_preserve_custom_names():
    client, calls = client_for()
    try:
        first = await client.get_entity_inventory(7, 102)
        assert first["next_page"] == 2 and first["complete"] is False
        assert first["missing_data"] == []
        compass, rope = first["data"]
        assert compass["name"] == "Compass"
        assert compass["item"] == {
            "id": 21,
            "entity_id": 201,
            "name": "Compass",
            "type": "Navigation",
            "is_private": None,
            "source": "https://api.kanka.io/1.0/campaigns/7/items/21",
        }
        assert compass["amount"] == "2" and compass["is_equipped"] is True
        assert rope["name"] == "Rope" and rope["item"] is None
        assert rope["is_equipped"] is False
        assert calls[:2] == [
            "/1.0/campaigns/7/entities/102",
            "/1.0/campaigns/7/entities/102/inventory",
        ]
        assert "/1.0/campaigns/7/items/21" in calls
        assert "/1.0/campaigns/7/entities/201" in calls
        last = await client.get_entity_inventory(7, 102, page=2)
        assert last["data"][0]["name"] == "Backup compass"
        assert last["next_page"] is None and last["complete"] is False
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"entity_id": 103},
        {"entity_id": True},
        {"item_id": "21"},
        {"item_id": True},
        {"amount": None},
        {"amount": True},
        {"is_equipped": 1},
        {"name": []},
    ],
)
async def test_malformed_inventory_fails_closed(changes):
    def override(request):
        response = handler(request)
        if request.url.path.endswith("/inventory"):
            payload = response.json()
            payload["data"][0].update(changes)
            return httpx.Response(200, json=payload)
        return response

    client, _ = client_for(override)
    try:
        with pytest.raises(KankaError):
            await client.get_entity_inventory(7, 102)
    finally:
        await client.aclose()


@pytest.mark.parametrize("status", [403, 404])
async def test_unavailable_inventory_item_is_explicit_and_payload_is_not_returned(
    status,
):
    def override(request):
        if request.url.path.endswith("/items/21"):
            return httpx.Response(status, text="SECRET CAMPAIGN CONTENT")
        return handler(request)

    client, _ = client_for(override)
    try:
        result = await client.get_entity_inventory(7, 102)
        assert result["complete"] is False
        assert result["data"][0]["item"] is None
        assert result["missing_data"] == [
            {
                "source": "https://api.kanka.io/1.0/campaigns/7/items/21",
                "reason": "unavailable",
            }
        ]
        assert "SECRET" not in str(result)
        assert "offline-test-token" not in str(result)
    finally:
        await client.aclose()


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_other_link_errors_cannot_be_misreported_as_missing(status):
    def override(request):
        if request.url.path.endswith("/items/21"):
            return httpx.Response(status, headers={"Retry-After": "60"})
        return handler(request)

    client, _ = client_for(override)
    try:
        with pytest.raises(KankaError) as error:
            await client.get_entity_inventory(7, 102)
        assert error.value.status_code == status
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "path, changes",
    [
        ("/items/21", {"id": 22}),
        ("/items/21", {"entity_id": True}),
        ("/items/21", {"campaign_id": 8}),
        ("/items/21", {"name": "Different"}),
        ("/entities/201", {"campaign_id": 8}),
        ("/entities/201", {"type": "character"}),
        ("/entities/201", {"child_id": 22}),
        ("/entities/201", {"child_id": True}),
    ],
)
async def test_linked_item_identity_cannot_cross_campaigns_or_module_ids(path, changes):
    def override(request):
        response = handler(request)
        if request.url.path.endswith(path):
            payload = response.json()
            payload["data"].update(changes)
            return httpx.Response(200, json=payload)
        return response

    client, _ = client_for(override)
    try:
        with pytest.raises(KankaError):
            await client.get_entity_inventory(7, 102)
    finally:
        await client.aclose()


async def test_duplicate_item_links_are_read_once_and_enrichment_limit_is_explicit(
    monkeypatch,
):
    monkeypatch.setattr("loremaster.kanka.MAX_LINKED_RECORDS", 1)

    def override(request):
        if request.url.path.endswith("/inventory"):
            row = {
                "id": 501,
                "entity_id": 102,
                "item_id": 21,
                "name": None,
                "amount": 1,
                "is_equipped": True,
                "item": {"entry": "UNVERIFIED NESTED LORE"},
            }
            return envelope(
                [
                    row,
                    {**row, "id": 502, "name": "Custom name"},
                    {**row, "id": 503, "item_id": 22},
                ]
            )
        return handler(request)

    client, calls = client_for(override)
    try:
        result = await client.get_entity_inventory(7, 102)
        assert calls.count("/1.0/campaigns/7/items/21") == 1
        assert calls.count("/1.0/campaigns/7/entities/201") == 1
        assert "/1.0/campaigns/7/items/22" not in calls
        assert [row["name"] for row in result["data"]] == [
            "Compass",
            "Custom name",
            None,
        ]
        assert result["missing_data"][0]["reason"] == "lookup_limit"
        assert result["next_page"] is None and result["complete"] is False
        assert "UNVERIFIED" not in str(result)
    finally:
        await client.aclose()


async def test_relations_preserve_duplicate_edges_direction_and_unavailable_target():
    client, calls = client_for()
    try:
        result = await client.get_entity_relations(7, 103)
        edges = result["data"]
        assert len(edges) == 4
        assert [row["relation"] for row in edges] == [
            "Mentor",
            "Rival",
            "Ally",
            "Unknown",
        ]
        assert [row["direction"] for row in edges] == [
            "outgoing",
            "outgoing",
            "incoming",
            "outgoing",
        ]
        assert edges[0]["target_name"] == "Áster" and edges[2]["owner_name"] == "Bryn"
        assert edges[3]["target_name"] is None
        assert calls.count("/1.0/campaigns/7/entities/101") == 1
        assert calls.count("/1.0/campaigns/7/entities/103") == 1
        assert result["missing_data"] == [
            {
                "source": "https://api.kanka.io/1.0/campaigns/7/entities/999",
                "reason": "unavailable",
            }
        ]
        assert result["complete"] is False and result["next_page"] is None
        assert "PRIVATE UPSTREAM PAYLOAD" not in str(result)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_id": True},
        {"target_id": "101"},
        {"owner_id": 102, "target_id": 101},
        {"relation": None},
        {"attitude": 101},
        {"attitude": True},
        {"is_pinned": 1},
    ],
)
async def test_malformed_relations_do_not_return_unrelated_or_invented_edges(changes):
    def override(request):
        if request.url.path.endswith("/relations"):
            return envelope(
                [{"owner_id": 103, "target_id": 101, "relation": "Ally", **changes}]
            )
        return handler(request)

    client, _ = client_for(override)
    try:
        with pytest.raises(KankaError):
            await client.get_entity_relations(7, 103)
    finally:
        await client.aclose()


async def test_self_relations_need_no_extra_lookup_and_limit_does_not_hide_edges(
    monkeypatch,
):
    monkeypatch.setattr("loremaster.kanka.MAX_LINKED_RECORDS", 0)

    def override(request):
        if request.url.path.endswith("/relations"):
            return envelope(
                [
                    {"owner_id": 103, "target_id": 103, "relation": "Self"},
                    {"owner_id": 103, "target_id": 101, "relation": "Ally"},
                ]
            )
        return handler(request)

    client, calls = client_for(override)
    try:
        result = await client.get_entity_relations(7, 103)
        assert result["data"][0]["direction"] == "self"
        assert result["data"][0]["target_name"] == "Cairn"
        assert result["data"][1]["target_id"] == 101
        assert result["data"][1]["target_name"] is None
        assert result["missing_data"][0]["reason"] == "lookup_limit"
        assert len(calls) == 2
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "method, entity_id",
    [
        ("get_entity_inventory", 102),
        ("get_entity_relations", 103),
        ("get_entity_posts", 101),
    ],
)
async def test_complete_empty_collection_is_distinct_from_missing_pagination(
    method, entity_id
):
    def override(request):
        if request.url.path.endswith(("/inventory", "/relations", "/posts")):
            return envelope([])
        return handler(request)

    client, _ = client_for(override)
    try:
        result = await getattr(client, method)(7, entity_id)
        assert result == {
            "data": [],
            "next_page": None,
            "complete": True,
            "missing_data": [],
        }
    finally:
        await client.aclose()


@pytest.mark.parametrize("method", ["get_entity_inventory", "get_entity_relations"])
@pytest.mark.parametrize("page", [True, 0, -1, "1"])
async def test_invalid_pages_never_issue_network_requests(method, page):
    client, calls = client_for()
    try:
        with pytest.raises(ValueError, match="page"):
            await getattr(client, method)(7, 102, page=page)
        assert calls == []
    finally:
        await client.aclose()


@pytest.mark.parametrize("method", ["get_entity_inventory", "get_entity_relations"])
async def test_disallowed_campaign_is_rejected_before_owner_or_collection_reads(method):
    client = KankaClient(
        "dummy",
        allowed_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected HTTP")),
    )
    try:
        with pytest.raises(PermissionError):
            await getattr(client, method)(8, 102)
    finally:
        await client.aclose()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_nonfinite_json_is_rejected_without_exposing_payload(constant):
    client = KankaClient(
        "dummy",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='{"data": ' + constant + "}")
        ),
    )
    try:
        with pytest.raises(KankaError, match="invalid JSON"):
            await client.list_campaigns()
    finally:
        await client.aclose()


async def test_generic_metadata_omits_embedded_child_lore():
    client, _ = client_for(
        lambda request: envelope({**entity(101), "child": {"entry": "Redundant lore"}})
    )
    try:
        record = await client.get_entity_metadata(7, 101)
        assert "child" not in record
        assert record["name"] == "Áster" and record["entity_type"] == "character"
    finally:
        await client.aclose()


async def test_anonymous_inventory_entry_is_explicitly_incomplete():
    def override(request):
        if request.url.path.endswith("/inventory"):
            return envelope(
                [
                    {
                        "id": 501,
                        "entity_id": 102,
                        "item_id": None,
                        "name": None,
                        "amount": 1,
                        "is_equipped": False,
                    }
                ]
            )
        return handler(request)

    client, _ = client_for(override)
    try:
        result = await client.get_entity_inventory(7, 102)
        assert len(result["data"]) == 1 and result["complete"] is False
        assert result["missing_data"][0]["reason"] == "missing_item_name"
    finally:
        await client.aclose()


async def test_oversized_page_stops_before_enrichment():
    def override(request):
        if request.url.path.endswith("/inventory"):
            return envelope([{"id": index + 1} for index in range(101)])
        return handler(request)

    client, calls = client_for(override)
    try:
        with pytest.raises(KankaError, match="page size safety limit"):
            await client.get_entity_inventory(7, 102)
        assert len(calls) == 2
    finally:
        await client.aclose()


async def test_relation_target_campaign_mismatch_is_a_failure_not_a_missing_link():
    def override(request):
        if request.url.path.endswith("/entities/101"):
            return envelope({**entity(101), "campaign_id": 8})
        return handler(request)

    client, _ = client_for(override)
    try:
        with pytest.raises(KankaError, match="different campaign"):
            await client.get_entity_relations(7, 103)
    finally:
        await client.aclose()


@pytest.mark.parametrize("method", ["find_entities_by_name", "search_entities"])
async def test_name_input_limit_is_checked_before_requests(method):
    client, calls = client_for()
    try:
        with pytest.raises(ValueError, match="1000"):
            await getattr(client, method)(7, "X" * 1001)
        assert calls == []
    finally:
        await client.aclose()
