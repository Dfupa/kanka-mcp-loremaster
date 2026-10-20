"""Full entity reads with synthetic permissions, quotas and calendar data."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fake_kanka import envelope, handler

from loremaster.aggregate import SECTIONS, get_entity_full
from loremaster.kanka import KankaClient, KankaError, ReadBudget
from loremaster.server import create_server


def make_client(override=None):
    """Inject a fully offline HTTP boundary; unexpected writes fail immediately."""

    def route(request):
        assert request.method == "GET"
        response = override(request) if override else None
        return response if response is not None else handler(request)

    return KankaClient("offline-token", transport=httpx.MockTransport(route))


async def test_full_entity_includes_all_direct_sections_and_preserves_dates():
    def override(request):
        if request.url.path.endswith("/attributes"):
            return envelope(
                [{"id": 1, "entity_id": 101, "name": "Strength", "value": "5"}]
            )
        if request.url.path.endswith("/reminders"):
            return envelope(
                [
                    {
                        "id": 2,
                        "remindable_id": 101,
                        "remindable_type": "App/Models/Entity",
                        "calendar_id": 33,
                        "date": "-12-17-55",
                        "is_recurring": True,
                    }
                ]
            )
        if request.url.path.endswith("/entity_assets"):
            return envelope(
                [{"id": 3, "entity_id": 101, "name": "Alias", "type_id": 3}]
            )

    client = make_client(override)
    try:
        result = await create_server(client).call_tool(
            "get_entity_full", {"campaign_id": 7, "entity_id": 101}
        )
        payload = result.structuredContent
        assert not result.isError
        assert payload["complete"] is True and payload["missing_data"] == []
        data = payload["data"]
        assert data["entity"]["name"] == "Áster"
        assert "café" in data["details"]["entry"]
        assert set(data["sections"]) == set(SECTIONS)
        assert data["sections"]["reminders"]["data"][0]["date"] == "-12-17-55"
        assert data["sections"]["reminders"]["data"][0]["calendar_id"] == 33
        assert data["sections"]["assets"]["data"][0]["type_id"] == 3
        assert data["sections"]["attributes"]["source"].endswith(
            "/entities/101/attributes"
        )
        assert data["sources"]["details"].endswith("/characters/11")
        assert data["not_requested"] == [] and data["continuation"] == {}
    finally:
        await client.aclose()


@pytest.mark.parametrize("per_page", [1, 45, 100, 150])
async def test_account_page_size_is_authoritative_not_a_tier_table(per_page):
    requests = []

    def override(request):
        requests.append(request)
        if request.url.path.endswith("/posts"):
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": (page - 1) * per_page + i + 1, "entity_id": 101}
                        for i in range(per_page)
                    ],
                    "meta": {
                        "current_page": page,
                        "last_page": 2,
                        "per_page": per_page,
                    },
                },
            )

    client = make_client(override)
    try:
        data = await get_entity_full(client, 7, 101, sections=["posts"])
        posts = data["sections"]["posts"]
        assert len(posts["data"]) == 2 * per_page
        assert posts["complete"] is True and posts["next_page"] is None
        assert data["missing_data"] == []
        assert all(
            "limit" not in r.url.params and "per_page" not in r.url.params
            for r in requests
        )
    finally:
        await client.aclose()


@pytest.mark.parametrize("rate_limit", [30, 90, 180])
async def test_quota_pacing_uses_reported_rate_and_hides_profile(
    rate_limit, monkeypatch
):
    sleep = AsyncMock()
    monkeypatch.setattr("loremaster.kanka.asyncio.sleep", sleep)

    def override(request):
        if request.url.path.endswith("/profile"):
            return envelope(
                {"rate_limit": rate_limit, "default_pagination": 25, "email": "PRIVATE"}
            )

    client = make_client(override)
    try:
        data = await get_entity_full(client, 7, 101, sections=[])
        assert data["account_limits"] == {
            "rate_limit": rate_limit,
            "default_pagination": 25,
        }
        assert "PRIVATE" not in str(data)
        delays = [call.args[0] for call in sleep.await_args_list]
        assert len(delays) == 2
        # The profile request now consumes a shared slot before both entity GETs.
        assert all(0 < delay <= 60 / rate_limit for delay in delays)
        assert client._budget.get() is None
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "section,status", [("attributes", 403), ("assets", 404), ("images", 403)]
)
async def test_unavailable_section_does_not_hide_other_sections(section, status):
    suffix = {"attributes": "attributes", "assets": "entity_assets", "images": "image"}[
        section
    ]
    client = make_client(
        lambda r: (
            httpx.Response(status, text="SECRET")
            if r.url.path.endswith("/" + suffix)
            else None
        )
    )
    try:
        data = await get_entity_full(client, 7, 101, sections=[section, "posts"])
        assert data["sections"][section]["data"] is None
        assert data["sections"][section]["status"] == "unavailable"
        assert data["sections"]["posts"]["complete"] is True
        assert data["missing_data"][0]["reason"] == f"http_{status}"
        assert "SECRET" not in str(data)
    finally:
        await client.aclose()


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_global_http_errors_fail_closed(status):
    client = make_client(
        lambda r: (
            httpx.Response(status, headers={"Retry-After": "31"})
            if r.url.path.endswith("/attributes")
            else None
        )
    )
    try:
        with pytest.raises(KankaError) as error:
            await get_entity_full(client, 7, 101, sections=["attributes"])
        assert error.value.status_code == status
        assert client._budget.get() is None
    finally:
        await client.aclose()


async def test_page_budget_returns_stateless_continuation(monkeypatch):
    monkeypatch.setattr("loremaster.aggregate.MAX_CAMPAIGN_PAGES", 1)

    def override(request):
        if request.url.path.endswith("/posts"):
            page = int(request.url.params["page"])
            return envelope([{"id": page, "entity_id": 101}], page=page, last=2)

    client = make_client(override)
    try:
        first = await get_entity_full(client, 7, 101, sections=["posts"])
        assert first["continuation"] == {"posts": 2}
        assert first["sections"]["posts"]["complete"] is False
        last = await get_entity_full(
            client, 7, 101, sections=["posts"], section_pages=first["continuation"]
        )
        assert last["sections"]["posts"]["data"][0]["id"] == 2
        assert last["continuation"] == {}
        assert last["sections"]["posts"]["complete"] is False
        assert last["missing_data"][0]["reason"] == "earlier_pages_not_included"
    finally:
        await client.aclose()


async def test_request_budget_defers_unfinished_sections_without_losing_core(
    monkeypatch,
):
    original = ReadBudget.wait

    async def limited(self):
        if self.remaining == 98:
            self.remaining = 0
        await original(self)

    monkeypatch.setattr(ReadBudget, "wait", limited)
    client = make_client()
    try:
        data = await get_entity_full(client, 7, 101, sections=["posts", "images"])
        assert data["entity"]["id"] == 101
        assert data["continuation"] == {"posts": 1, "images": 1}
        assert all(part["status"] == "deferred" for part in data["sections"].values())
        assert all(item["reason"] == "read_budget" for item in data["missing_data"])
    finally:
        await client.aclose()


@pytest.mark.parametrize("changes", [{"entity_id": 999}, {"campaign_id": 8}])
async def test_attachment_identity_mismatch_fails_closed(changes):
    client = make_client(
        lambda r: (
            envelope([{"id": 1, "entity_id": 101, **changes}])
            if r.url.path.endswith("/attributes")
            else None
        )
    )
    try:
        with pytest.raises(KankaError, match="different"):
            await get_entity_full(client, 7, 101, sections=["attributes"])
    finally:
        await client.aclose()


async def test_missing_pagination_cannot_claim_empty_section_complete():
    client = make_client(
        lambda r: (
            httpx.Response(200, json={"data": []})
            if r.url.path.endswith("/attributes")
            else None
        )
    )
    try:
        data = await get_entity_full(client, 7, 101, sections=["attributes"])
        part = data["sections"]["attributes"]
        assert part["data"] == [] and part["complete"] is False
        assert part["missing_data"][0]["reason"] == "pagination_unavailable"
        assert data["continuation"] == {}
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sections": ["bad"]},
        {"sections": ["posts", "posts"]},
        {"sections": [], "section_pages": {"posts": 1}},
        {"section_pages": {"posts": True}},
        {"section_pages": {"images": 2}},
    ],
)
async def test_invalid_selection_or_continuation_fails_before_http(kwargs):
    calls = []
    client = make_client(lambda r: calls.append(r))
    try:
        with pytest.raises(ValueError):
            await get_entity_full(client, 7, 101, **kwargs)
        assert calls == []
    finally:
        await client.aclose()


async def test_disallowed_campaign_fails_before_profile_read():
    calls = []
    client = KankaClient(
        "dummy",
        allowed_campaign_ids=frozenset({7}),
        transport=httpx.MockTransport(lambda r: calls.append(r)),
    )
    try:
        with pytest.raises(PermissionError):
            await get_entity_full(client, 8, 101)
        assert calls == []
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "profile",
    [
        None,
        {},
        {"rate_limit": True, "default_pagination": 45},
        {"rate_limit": 0, "default_pagination": 45},
        {"rate_limit": 90, "default_pagination": "100"},
    ],
)
async def test_invalid_account_metadata_stops_without_guessing_tier(profile):
    requests = []

    def override(request):
        requests.append(request)
        return envelope(profile)

    client = make_client(override)
    try:
        with pytest.raises(KankaError, match="quota metadata"):
            await get_entity_full(client, 7, 101)
        assert len(requests) == 1 and requests[0].url.path.endswith("/profile")
    finally:
        await client.aclose()


@pytest.mark.parametrize("per_page", [True, 0, -1, "45"])
async def test_malformed_effective_page_size_fails_closed(per_page):
    client = make_client(
        lambda r: (
            httpx.Response(
                200,
                json={
                    "data": [],
                    "meta": {"last_page": 1, "per_page": per_page},
                },
            )
            if r.url.path.endswith("/posts")
            else None
        )
    )
    try:
        with pytest.raises(KankaError, match="per_page"):
            await get_entity_full(client, 7, 101, sections=["posts"])
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "images",
    [
        None,
        {},
        {"image": "invalid", "header": None},
        {"image": {"uuid": 42}, "header": None},
    ],
)
async def test_malformed_images_do_not_appear_complete(images):
    client = make_client(
        lambda r: (
            httpx.Response(200, json={"data": images})
            if r.url.path.endswith("/image")
            else None
        )
    )
    try:
        with pytest.raises(KankaError, match="image"):
            await get_entity_full(client, 7, 101, sections=["images"])
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "owner,type_name",
    [(999, "App/Models/Entity"), (101, "App/Models/Post"), (True, "App/Models/Entity")],
)
async def test_reminders_cannot_cross_subjects_or_model_types(owner, type_name):
    client = make_client(
        lambda r: (
            envelope(
                [
                    {
                        "id": 1,
                        "remindable_id": owner,
                        "remindable_type": type_name,
                    }
                ]
            )
            if r.url.path.endswith("/reminders")
            else None
        )
    )
    try:
        with pytest.raises(KankaError, match="different subject"):
            await get_entity_full(client, 7, 101, sections=["reminders"])
    finally:
        await client.aclose()


async def test_budget_deadline_stops_before_sending_and_releases_context():
    client = make_client()
    try:
        async with client.account_reads():
            budget = client._budget.get()
            budget.deadline = 0
            with pytest.raises(KankaError) as error:
                await client.get_entity(7, 101)
            assert error.value.code == "read_budget_exhausted"
        assert client._budget.get() is None
    finally:
        await client.aclose()


async def test_budget_interruption_preserves_completed_pages(monkeypatch):
    original = ReadBudget.wait

    async def limited(self):
        if self.remaining == 97:
            self.remaining = 0
        await original(self)

    monkeypatch.setattr(ReadBudget, "wait", limited)

    def override(request):
        if request.url.path.endswith("/posts"):
            page = int(request.url.params["page"])
            return envelope([{"id": page, "entity_id": 101}], page=page, last=3)

    client = make_client(override)
    try:
        data = await get_entity_full(client, 7, 101, sections=["posts", "images"])
        assert data["sections"]["posts"]["data"] == [{"id": 1, "entity_id": 101}]
        assert data["continuation"] == {"posts": 2, "images": 1}
        assert data["sections"]["posts"]["complete"] is False
    finally:
        await client.aclose()


@pytest.mark.parametrize("preference", [0, None])
async def test_unset_account_pagination_does_not_override_effective_page_size(
    preference,
):
    client = make_client(
        lambda r: (
            envelope(
                {
                    "rate_limit": 1_000_000,
                    "default_pagination": preference,
                }
            )
            if r.url.path.endswith("/profile")
            else None
        )
    )
    try:
        data = await get_entity_full(client, 7, 101, sections=["posts"])
        assert data["account_limits"]["default_pagination"] is preference
        assert data["sections"]["posts"]["complete"] is True
    finally:
        await client.aclose()
