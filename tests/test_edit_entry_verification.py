"""Plain-text paragraph normalization never relaxes untouched-field guards."""

import pytest
from test_editing import approve, draft
from test_editing import editor as editor

from loremaster.edit_models import EditDraft
from loremaster.editing import entry_matches


@pytest.mark.parametrize(
    "expected,actual,matches",
    [
        ("Élan", "Élan", True),
        ("Élan", "<p>Élan</p>", True),
        ('A & "B"', '<p>A &amp; "B"</p>', True),
        ("A", "<p>B</p>", False),
        ("A", '<p class="x">A</p>', False),
        ("A", "<p>A</p><p></p>", False),
        ("A", "<p><b>A</b></p>", False),
        ("<b>A</b>", "<p><b>A</b></p>", False),
        ("<b>A</b>", "<b>A</b>", True),
        ("A", None, False),
        (None, None, False),
    ],
)
def test_entry_equivalence_is_narrow(expected, actual, matches):
    assert entry_matches(expected, actual) is matches


@pytest.mark.parametrize("kind", ["post", "character", "race"])
async def test_requested_plain_entry_can_be_wrapped(editor, kind):
    service, api = editor
    edit = (
        EditDraft(campaign_id=7, entity_id=201, kind="race", changes={"entry": "A & B"})
        if kind == "race"
        else draft(kind, {"entry": "A & B"})
    )
    proposal = await service.prepare(edit)

    def wrap(request, response):
        if request.method == "PATCH":
            record = (
                api.attachments["posts"][0]
                if kind == "post"
                else api.records[edit.entity_id]
            )
            record["entry"] = "<p>A &amp; B</p>"
        return response

    api.response_hook = wrap
    approve(service, proposal)
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert len([row for row in api.requests if row[0] == "PATCH"]) == 1


async def test_untouched_entry_normalization_still_fails(editor):
    service, api = editor
    api.attachments["posts"][0]["entry"] = "Original"
    proposal = await service.prepare(draft("post", {"name": "New title"}))

    def wrap(request, response):
        if request.method == "PATCH":
            api.attachments["posts"][0]["entry"] = "<p>Original</p>"
        return response

    api.response_hook = wrap
    approve(service, proposal)
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "partial"


async def test_confirmed_entry_retry_only_reads(editor):
    service, api = editor
    proposal = await service.prepare(draft("post", {"entry": "New text"}))

    def changed(request, response):
        if request.method == "PATCH":
            api.attachments["posts"][0]["entry"] = "Different"
        return response

    api.response_hook = changed
    approve(service, proposal)
    result = await service.execute(7, proposal["proposal_id"])
    assert result["status"] == "partial" and result["outcome"] == "confirmed"
    api.attachments["posts"][0]["entry"] = "<p>New text</p>"
    checkpoint = len(api.requests)
    approve(service, proposal)
    assert (await service.execute(7, proposal["proposal_id"]))["status"] == "verified"
    assert all(method == "GET" for method, _, _ in api.requests[checkpoint:])
