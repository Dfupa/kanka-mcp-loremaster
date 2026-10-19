"""Current live-style search candidates are not a complete, paginated entity index."""

import httpx
import pytest
from fake_kanka import handler

from loremaster.kanka import KankaClient, KankaError


def search_client(data, **envelope):
    """Return synthetic search rows and verify identities through the real client."""
    calls = []

    def override(request):
        calls.append(request.url.path)
        if "/search/" in request.url.path:
            return httpx.Response(200, json={"data": data, **envelope})
        return handler(request)

    return KankaClient("dummy", transport=httpx.MockTransport(override)), calls


async def test_unpaged_search_verifies_universal_ids_and_uses_generic_child_ids():
    rows = [{"id": 101, "child_id": 999, "name": "Áster", "entity_type": "character"}]
    client, calls = search_client(rows, sync="synthetic")
    try:
        result = await client.search_entities(7, "Áster")
        assert result["data"][0]["entity_id"] == 101
        assert result["data"][0]["child_id"] == 11
        assert result["complete"] is False and result["next_page"] is None
        assert result["missing_data"][0]["reason"] == "pagination_unavailable"
        assert calls[-1] == "/1.0/campaigns/7/entities/101"
        with pytest.raises(KankaError, match="pagination is unavailable"):
            await client.search_entities(7, "Áster", page=2)
    finally:
        await client.aclose()


async def test_empty_unpaged_search_cannot_claim_absence():
    client, _ = search_client([])
    try:
        result = await client.search_entities(7, "Missing")
        assert result["data"] == [] and result["complete"] is False
        assert result["missing_data"][0]["reason"] == "pagination_unavailable"
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "Different"},
        {"entity_id": True},
        {"campaign_id": 8},
    ],
)
async def test_search_rejects_inconsistent_record_identity(changes):
    client, _ = search_client([{"id": 101, "name": "Áster", **changes}])
    try:
        with pytest.raises(KankaError):
            await client.search_entities(7, "Áster")
    finally:
        await client.aclose()


async def test_search_identity_budget_fails_before_extra_metadata_requests(monkeypatch):
    monkeypatch.setattr("loremaster.kanka.MAX_LINKED_RECORDS", 1)
    client, calls = search_client(
        [{"id": 101, "name": "Áster"}, {"id": 102, "name": "Bryn"}]
    )
    try:
        with pytest.raises(KankaError, match="too many candidates"):
            await client.search_entities(7, "Broad")
        assert len(calls) == 1
    finally:
        await client.aclose()
