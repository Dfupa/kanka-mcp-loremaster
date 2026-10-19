"""Regression checks for observed generic schemas and identity boundaries."""

from unittest.mock import AsyncMock

import httpx
import pytest

from loremaster.kanka import KankaClient, KankaError


def generic(**changes):
    """Return synthetic current-schema data, never a campaign response dump."""
    return {
        "id": 42,
        "campaign_id": 7,
        "child_id": 5,
        "name": "Ávilon",
        "type": "character",
        "type_field": "Scholar",
        **changes,
    }


def character(**changes):
    """Build a module record with a distinct child and universal entity ID."""
    return {"id": 5, "entity_id": 42, "name": "Ávilon", "type": "Scholar", **changes}


@pytest.mark.parametrize(
    "type_fields",
    [
        {},
        {"entity_type": "character", "type": "Scholar"},
        {"entity_type_code": "character", "type": "Scholar"},
    ],
)
async def test_current_and_legacy_schemas_resolve_both_ids(type_fields):
    requests = []

    def handler(request):
        assert request.method == "GET"
        requests.append(request.url.path)
        if request.url.path.endswith("/entities"):
            assert request.url.params["name"] == "A\u0301VILON"
            return httpx.Response(
                200, json={"data": [generic(**type_fields)], "meta": {"last_page": 1}}
            )
        if request.url.path.endswith("/entities/42"):
            data = generic(**type_fields)
        else:
            assert request.url.path.endswith("/characters/5")
            data = character()
        return httpx.Response(200, json={"data": data})

    client = KankaClient("test-token", transport=httpx.MockTransport(handler))
    try:
        matches = await client.find_entities_by_name(7, " A\u0301VILON ")
        assert matches["partial"] == []
        assert matches["exact"] == [
            {
                "campaign_id": 7,
                "entity_id": 42,
                "child_id": 5,
                "name": "Ávilon",
                "entity_type": "character",
            }
        ]
        result = await client.get_entity(7, matches["exact"][0]["entity_id"])
        assert result["entity"]["entity_type"] == "character"
        assert result["details"] == character()
        assert len(requests) == 3
    finally:
        await client.aclose()


async def test_substring_filter_does_not_fabricate_partial_matches():
    client = KankaClient(
        "test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": [
                        generic(name="Sir Ávilon"),
                        generic(id=43, name="Avilon"),
                        generic(id=44, name="Unrelated"),
                    ],
                    "meta": {"last_page": 1},
                },
            )
        ),
    )
    try:
        result = await client.find_entities_by_name(7, "Ávilon")
        assert result["exact"] == []
        assert [row["name"] for row in result["partial"]] == ["Sir Ávilon"]
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"entity_id": 99},
        {"entity_id": None},
        {"entity_id": True},
        {"campaign_id": 8},
        {"campaign_id": True},
        {"name": "Different"},
        {"name": None},
    ],
)
async def test_module_identity_mismatch_fails_closed(changes):
    def handler(request):
        data = (
            generic()
            if request.url.path.endswith("/entities/42")
            else character(**changes)
        )
        return httpx.Response(200, json={"data": data})

    client = KankaClient("test-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(KankaError, match="mismatch"):
            await client.get_entity(7, 42)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"campaign_id": 8},
        {"campaign_id": True},
        {"type": []},
        {"type": None},
        {"child_id": True},
        {"child_id": None},
    ],
)
async def test_invalid_generic_details_fail_before_module_request(changes):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": generic(**changes)})

    client = KankaClient("test-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(KankaError):
            await client.get_entity(7, 42)
        assert len(calls) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "pagination",
    [
        {},
        {"meta": {"last_page": None}},
        {"meta": {"current_page": 2, "last_page": 2}},
        {"meta": {"current_page": True}},
        {"links": {"next": True}},
        {"meta": {"last_page": 2}, "links": {"next": None}},
        {"meta": {"last_page": 1}, "links": {"next": "https://example.invalid"}},
    ],
)
async def test_inconsistent_pagination_cannot_claim_completeness(pagination):
    client = KankaClient(
        "test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [], **pagination})
        ),
    )
    try:
        with pytest.raises(KankaError, match="pagination"):
            await client.find_entities_by_name(7, "Missing")
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "retry_after, expected", [("3", [3]), ("invalid", [2]), ("60", [])]
)
async def test_rate_limit_retry_is_bounded_and_honors_wait(
    monkeypatch, retry_after, expected
):
    sleep = AsyncMock()
    monkeypatch.setattr("loremaster.kanka.asyncio.sleep", sleep)
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": retry_after}),
            httpx.Response(200, json={"data": [], "meta": {"last_page": 1}}),
        ]
    )
    client = KankaClient(
        "test-token", transport=httpx.MockTransport(lambda request: next(responses))
    )
    try:
        if expected:
            assert await client.list_campaigns() == []
        else:
            with pytest.raises(KankaError, match="429"):
                await client.list_campaigns()
        assert [call.args[0] for call in sleep.await_args_list] == expected
    finally:
        await client.aclose()


async def test_repeated_rate_limits_stop_after_three_requests(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("loremaster.kanka.asyncio.sleep", sleep)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429)

    client = KankaClient("test-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(KankaError, match="429"):
            await client.list_campaigns()
        assert len(requests) == 3
        assert [call.args[0] for call in sleep.await_args_list] == [2, 4]
    finally:
        await client.aclose()


async def test_transport_error_is_sanitized():
    def handler(request):
        raise httpx.ReadTimeout("test-token private response", request=request)

    client = KankaClient("test-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(KankaError, match="timed out") as error:
            await client.list_campaigns()
        assert "test-token" not in str(error.value)
        assert "private response" not in str(error.value)
    finally:
        await client.aclose()
