"""Offline Kanka API contract and security checks."""

import httpx
import pytest

from loremaster.kanka import KankaClient, KankaError


def mock_client(handler, *, allowed=None):
    """Create a Kanka client with an intercepted HTTP transport."""
    return KankaClient(
        "example-test-token",
        transport=httpx.MockTransport(handler),
        allowed_campaign_ids=allowed,
    )


@pytest.mark.parametrize("token", ["", " ", None])
def test_token_is_required(token):
    with pytest.raises(ValueError, match="KANKA_API_TOKEN"):
        KankaClient(token)


@pytest.mark.parametrize("campaign_id", [0, -1, True, "42"])
@pytest.mark.asyncio
async def test_rejects_invalid_campaigns_before_requests(campaign_id):
    client = mock_client(lambda request: pytest.fail("Unexpected network request"))
    try:
        with pytest.raises(ValueError, match="campaign_id"):
            await client.search_entities(campaign_id, "Weiss")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_filters_campaigns_across_pages():
    requested = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer example-test-token"
        assert request.url.path == "/1.0/campaigns"
        page = int(request.url.params["page"])
        requested.append(page)
        return httpx.Response(
            200,
            json={
                "data": [{"id": page, "name": f"Campaign {page}"}],
                "meta": {"last_page": 2},
            },
        )

    client = mock_client(handler, allowed=frozenset({2}))
    try:
        assert await client.list_campaigns() == [{"id": 2, "name": "Campaign 2"}]
        assert requested == [1, 2]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_encodes_unicode_and_preserves_entity_ids():
    def handler(request):
        assert request.url.path.endswith("/search/Ávilon Weiss")
        assert request.url.params["page"] == "2"
        return httpx.Response(
            200,
            json={"data": [{"id": 5, "entity_id": 42, "name": "Weiss"}]},
        )

    client = mock_client(handler)
    try:
        result = await client.search_entities(7, " Ávilon Weiss ", page=2)
        assert result["data"][0]["entity_id"] == 42
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_entity_uses_child_id_and_related_flag():
    requested = []

    def handler(request):
        requested.append(request.url.path)
        if request.url.path.endswith("/entities/42"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": 42,
                        "name": "Weiss",
                        "entity_type": "character",
                        "child_id": 5,
                        "campaign_id": 7,
                    }
                },
            )
        assert request.url.path.endswith("/characters/5")
        assert request.url.params["related"] == "1"
        return httpx.Response(200, json={"data": {"id": 5, "entry": "Lore"}})

    client = mock_client(handler)
    try:
        result = await client.get_entity(7, 42)
        assert result["entity"]["id"] == 42
        assert result["details"] == {"id": 5, "entry": "Lore"}
        assert len(requested) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_custom_entity_is_not_guessed():
    def handler(request):
        assert request.url.path.endswith("/entities/42")
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": 42,
                    "campaign_id": 7,
                    "entity_type": "custom-module",
                    "child_id": 1,
                }
            },
        )

    client = mock_client(handler)
    try:
        assert (await client.get_entity(7, 42))["details"] is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_does_not_request_disallowed_campaign():
    client = mock_client(
        lambda request: pytest.fail("Unexpected network request"),
        allowed=frozenset({7}),
    )
    try:
        with pytest.raises(PermissionError, match="allowlist"):
            await client.get_entity_posts(8, 42)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_posts_use_universal_entity_id():
    def handler(request):
        assert request.url.path == "/1.0/campaigns/7/entities/42/posts"
        return httpx.Response(200, json={"data": [{"id": 55, "name": "History"}]})

    client = mock_client(handler)
    try:
        result = await client.get_entity_posts(7, 42)
        assert result["data"][0]["name"] == "History"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
async def test_http_errors_do_not_expose_token(status):
    client = mock_client(lambda request: httpx.Response(status))
    try:
        with pytest.raises(KankaError) as error:
            await client.list_campaigns()
        assert str(status) in str(error.value)
        assert "example-test-token" not in str(error.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_invalid_json_and_missing_data_are_rejected():
    responses = iter(
        [httpx.Response(200, text="broken"), httpx.Response(200, json={})]
    )
    client = mock_client(lambda request: next(responses))
    try:
        with pytest.raises(KankaError, match="invalid JSON"):
            await client.list_campaigns()
        with pytest.raises(KankaError, match="envelope"):
            await client.list_campaigns()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_mismatched_campaign_is_rejected():
    client = mock_client(
        lambda request: httpx.Response(
            200, json={"data": {"id": 42, "campaign_id": 8}}
        )
    )
    try:
        with pytest.raises(KankaError, match="different campaign"):
            await client.get_entity(7, 42)
    finally:
        await client.aclose()
