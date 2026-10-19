"""Offline smoke-check tests without real campaign data or credentials."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from loremaster.kanka import KankaClient, KankaError

spec = spec_from_file_location(
    "verify_character_lookup",
    Path(__file__).resolve().parents[1] / "scripts" / "verify_character_lookup.py",
)
assert spec is not None and spec.loader is not None
verifier = module_from_spec(spec)
spec.loader.exec_module(verifier)


def synthetic_client(mode="valid"):
    """Provide current-schema responses with different child and entity IDs."""
    names = ("North", "South", "West")

    def handler(request):
        assert request.method == "GET"
        path = request.url.path
        if path.endswith("/campaigns"):
            rows = [{"id": 7}]
        elif path.endswith("/entities"):
            index = names.index(request.url.params["name"])
            rows = [
                {
                    "id": index + 40,
                    "child_id": index + 5,
                    "campaign_id": 7,
                    "name": names[index],
                    "type": "character",
                    "type_field": "Scholar",
                }
            ]
            if mode == "ambiguous":
                rows.append({**rows[0], "id": 99})
            elif mode == "missing":
                rows = []
            elif mode == "noncharacter":
                rows[0]["type"] = "location"
            elif mode == "error":
                return httpx.Response(403, text="Private payload must not be printed")
        elif "/entities/" in path:
            index = int(path.rsplit("/", 1)[1]) - 40
            rows = {
                "id": index + 40,
                "child_id": index + 5,
                "campaign_id": 7,
                "name": names[index],
                "type": "character",
                "type_field": "Scholar",
            }
        else:
            assert "/characters/" in path
            index = int(path.rsplit("/", 1)[1]) - 5
            rows = {"id": index + 5, "entity_id": index + 40, "name": names[index]}
            if mode == "mismatch":
                rows["entity_id"] = 999
        return httpx.Response(200, json={"data": rows, "meta": {"last_page": 1}})

    return KankaClient("test-token", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("valid", True),
        ("ambiguous", False),
        ("missing", False),
        ("noncharacter", False),
        ("error", None),
        ("mismatch", None),
    ],
)
async def test_verification_requires_unique_confirmed_characters(
    monkeypatch, capsys, mode, expected
):
    monkeypatch.setattr(verifier, "NAMES", ("North", "South", "West"))
    client = synthetic_client(mode)
    try:
        assert await verifier.verify_campaign(client, 7) is expected
    finally:
        await client.aclose()
    output = capsys.readouterr().out
    for sensitive in ("North", "South", "West", "test-token", "Private payload"):
        assert sensitive not in output


@pytest.mark.parametrize(
    "visible, statuses, expected_calls, expected",
    [
        ([7], [False, True], [7, 33742], True),
        ([33742], [True], [33742], True),
        ([33742], [False], [33742], False),
        ([33742], [None, True], [33742, 33742], True),
        ([7, 7], [False, False], [7, 33742], False),
        (list(range(1, 7)), [], [], False),
    ],
)
async def test_campaign_order_and_inconclusive_fallback(
    monkeypatch, visible, statuses, expected_calls, expected
):
    client = AsyncMock()
    client.list_campaigns.return_value = [{"id": value} for value in visible]
    check = AsyncMock(side_effect=statuses)
    monkeypatch.setattr(verifier, "verify_campaign", check)
    assert await verifier.verify(client) is expected
    assert [call.args[1] for call in check.await_args_list] == expected_calls


async def test_discovery_error_tries_only_explicit_fallback(monkeypatch):
    client = AsyncMock()
    client.list_campaigns.side_effect = KankaError("Synthetic failure")
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(verifier, "verify_campaign", check)
    assert await verifier.verify(client) is False
    check.assert_awaited_once_with(client, verifier.FALLBACK_CAMPAIGN_ID)


async def test_request_guard_rejects_writes_and_exhausted_budget(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(verifier.asyncio, "sleep", sleep)
    budget = verifier.ReadOnlyBudget()
    with pytest.raises(KankaError, match="budget"):
        await budget(httpx.Request("POST", "https://api.kanka.io/"))
    assert budget.requests == 0
    read = httpx.Request("GET", "https://api.kanka.io/1.0/campaigns")
    await budget(read)
    await budget(read)
    assert budget.requests == 2
    sleep.assert_awaited_once()
    budget.requests = verifier.MAX_REQUESTS
    with pytest.raises(KankaError, match="budget"):
        await budget(read)


async def test_main_rejects_missing_token(monkeypatch):
    monkeypatch.delenv("KANKA_API_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="not configured"):
        await verifier.main()


@pytest.mark.parametrize("success", [True, False])
async def test_main_closes_client_on_success_and_failure(monkeypatch, success):
    client = synthetic_client()
    monkeypatch.setenv("KANKA_API_TOKEN", "test-token")
    monkeypatch.setattr(verifier, "KankaClient", lambda token: client)
    monkeypatch.setattr(verifier, "verify", AsyncMock(return_value=success))
    if success:
        await verifier.main()
    else:
        with pytest.raises(SystemExit) as error:
            await verifier.main()
        assert error.value.code == 1
    assert client._http.is_closed
