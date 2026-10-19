"""Validate live-verifier logic with synthetic MCP results and no real secrets."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fake_kanka import handler

from loremaster.kanka import KankaClient
from loremaster.server import create_server

spec = spec_from_file_location(
    "verify_mcp", Path(__file__).resolve().parents[1] / "scripts" / "verify_mcp.py"
)
assert spec is not None and spec.loader is not None
verifier = module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.fixture
async def session(monkeypatch):
    """Use real tool functions while ensuring every linked test record is visible."""

    def complete_handler(request):
        response = handler(request)
        if request.url.path.endswith("/relations"):
            payload = response.json()
            payload["data"] = [
                row for row in payload["data"] if row["target_id"] != 999
            ]
            return httpx.Response(200, json=payload)
        return response

    client = KankaClient(
        "private-test-token", transport=httpx.MockTransport(complete_handler)
    )
    server = create_server(client)
    monkeypatch.setattr(verifier, "NAMES", ("Áster", "Bryn", "Cairn"))
    monkeypatch.setattr(verifier.asyncio, "sleep", AsyncMock())
    session = SimpleNamespace(
        list_tools=AsyncMock(
            return_value=SimpleNamespace(tools=await server.list_tools())
        ),
        call_tool=AsyncMock(side_effect=server.call_tool),
    )
    try:
        yield session
    finally:
        await client.aclose()


async def test_three_demonstrations_and_all_seven_tools_without_private_output(
    session, capsys
):
    await verifier.verify_session(session)
    output = capsys.readouterr().out
    assert "Description check: PASS" in output
    assert "Inventory check: PASS" in output
    assert "Relations check: PASS" in output
    assert "Search and posts check: PASS" in output
    assert {call.args[0] for call in session.call_tool.await_args_list} == {
        "list_campaigns",
        "find_entities_by_name",
        "get_entity",
        "get_entity_inventory",
        "get_entity_relations",
        "search_entities",
        "get_entity_posts",
    }
    for sensitive in (
        "Áster",
        "Bryn",
        "Cairn",
        "Compass",
        "Synthetic description",
        "private-test-token",
    ):
        assert sensitive not in output


@pytest.mark.parametrize(
    "limit_name, value, message",
    [
        ("MAX_TOOL_CALLS", 1, "budget"),
        ("MAX_PAGES", 1, "pagination safety"),
    ],
)
async def test_verifier_limits_cannot_pass_incomplete_results(
    session, monkeypatch, limit_name, value, message
):
    monkeypatch.setattr(verifier, limit_name, value)
    with pytest.raises(RuntimeError, match=message):
        await verifier.verify_session(session)


@pytest.mark.parametrize(
    "mode, message",
    [
        ("missing", "missing data"),
        ("error", "MCP read failed"),
        ("incomplete", "Incomplete campaign discovery"),
        ("ambiguous", "ambiguous"),
        ("changed", "changed character"),
    ],
)
async def test_verifier_rejects_missing_ambiguous_or_changed_records(
    session, mode, message
):
    underlying = session.call_tool.side_effect

    async def modify(tool, arguments):
        result = await underlying(tool, arguments)
        if tool == "list_campaigns":
            if mode == "missing":
                result.structuredContent["missing_data"] = [
                    {"source": "test", "reason": "unavailable"}
                ]
            elif mode == "error":
                result.isError = True
            elif mode == "incomplete":
                result.structuredContent["complete"] = False
        if tool == "find_entities_by_name" and mode == "ambiguous":
            result.structuredContent["data"]["exact"] *= 2
        if tool == "get_entity" and mode == "changed":
            result.structuredContent["data"]["entity"]["child_id"] = 999
        return result

    session.call_tool.side_effect = modify
    with pytest.raises(RuntimeError, match=message):
        await verifier.verify_session(session)


async def test_main_requires_credentials_without_starting_a_process(monkeypatch):
    monkeypatch.delenv("KANKA_API_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="not configured"):
        await verifier.main()


async def test_main_does_not_print_private_exception_details(monkeypatch):
    monkeypatch.setenv("KANKA_API_TOKEN", "private-test-token")

    def fail(*args, **kwargs):
        raise RuntimeError("private-test-token PRIVATE RESPONSE")

    monkeypatch.setattr(verifier, "stdio_client", fail)
    with pytest.raises(SystemExit) as error:
        await verifier.main()
    assert "private-test-token" not in str(error.value)
    assert "PRIVATE RESPONSE" not in str(error.value)
