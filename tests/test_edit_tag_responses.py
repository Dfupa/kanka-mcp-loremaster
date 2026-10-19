"""Tag detail ID omissions require corroborated list identity, never guessing."""

import httpx
import pytest
from test_editing import approve, draft, run
from test_editing import editor as editor

from loremaster.kanka import KankaError


def omit_id(request, response):
    if request.url.path.endswith("/entity_tags/91"):
        payload = response.json()
        payload["data"].pop("id", None)
        return httpx.Response(response.status_code, json=payload)
    return response


async def test_live_style_tag_detail_and_patch_require_list_readback(editor):
    service, api = editor
    api.response_hook = omit_id
    proposal, result = await run(service, draft("tag", {"tag_entity_id": 402}))
    assert result["status"] == "verified"
    assert api.records[101]["tags"] == [42]
    assert proposal["plan"]["before"]["record"]["id"] == 91
    assert sum(path.endswith("/entity_tags") for _, path, _ in api.requests) == 3


@pytest.mark.parametrize(
    "problem", ["missing", "duplicate", "wrong_tag", "wrong_owner"]
)
async def test_missing_tag_detail_id_fails_on_inconsistent_list(editor, problem):
    service, api = editor

    def hook(request, response):
        response = omit_id(request, response)
        if request.url.path.endswith("/entity_tags"):
            payload = response.json()
            row = payload["data"][0]
            if problem == "missing":
                payload["data"] = []
            elif problem == "duplicate":
                payload["data"].append(row.copy())
            elif problem == "wrong_tag":
                row["tag_id"] = 42
            else:
                row["entity_id"] = 102
            return httpx.Response(200, json=payload)
        return response

    api.response_hook = hook
    with pytest.raises(KankaError):
        await service.prepare(draft("tag", {"tag_entity_id": 402}))
    assert all(method == "GET" for method, _, _ in api.requests)


async def test_missing_patch_id_with_wrong_tag_stays_uncertain(editor):
    service, api = editor
    proposal = await service.prepare(draft("tag", {"tag_entity_id": 402}))

    def hook(request, response):
        if request.method == "PATCH":
            return httpx.Response(200, json={"data": {"tag_id": 41}})
        return response

    api.response_hook = hook
    approve(service, proposal)
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "uncertain"
    with pytest.raises(PermissionError):
        approve(service, proposal)
