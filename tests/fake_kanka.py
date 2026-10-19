"""Synthetic HTTP responses shared by client and subprocess transport tests."""

import httpx

CAMPAIGN_ID = 7
NAMES = {101: "Áster", 102: "Bryn", 103: "Cairn", 201: "Compass"}


def entity(entity_id: int) -> dict:
    """Build a generic record with a distinct module-specific child ID."""
    return {
        "id": entity_id,
        "campaign_id": CAMPAIGN_ID,
        "name": NAMES[entity_id],
        "type": "item" if entity_id == 201 else "character",
        "child_id": 21 if entity_id == 201 else entity_id - 90,
        "type_field": "Test category",
        "is_private": False,
    }


def envelope(data: object, *, page: int = 1, last: int = 1) -> httpx.Response:
    """Supply explicit pagination metadata for a synthetic API response."""
    return httpx.Response(
        200,
        json={
            "data": data,
            "meta": {"current_page": page, "last_page": last},
        },
    )


def handler(request: httpx.Request) -> httpx.Response:
    """Handle only known GET endpoints; never make an actual HTTP request."""
    if request.method != "GET":
        raise RuntimeError("Unexpected non-GET request")
    path = request.url.path.removeprefix("/1.0/")
    page = int(request.url.params.get("page", 1))
    if path == "profile":
        return envelope({"rate_limit": 1_000_000, "default_pagination": 45})
    if path == "campaigns":
        return envelope([{"id": CAMPAIGN_ID, "name": "Synthetic campaign"}])
    if path == "campaigns/7/entities":
        name = request.url.params.get("name", "").casefold()
        return envelope(
            [entity(key) for key, value in NAMES.items() if name in value.casefold()]
        )
    if path.startswith("campaigns/7/search/"):
        return envelope([{"id": 11, "entity_id": 101, "name": NAMES[101]}], page=page)
    if path.startswith("campaigns/7/characters/"):
        child_id = int(path.rsplit("/", 1)[1])
        return envelope(
            {
                "id": child_id,
                "entity_id": child_id + 90,
                "name": NAMES[child_id + 90],
                "entry": "<p>Synthetic description with Unicode: café.</p>",
            }
        )
    if path == "campaigns/7/items/21":
        return envelope(
            {"id": 21, "entity_id": 201, "name": "Compass", "type": "Navigation"}
        )
    if path == "campaigns/7/items/22":
        return httpx.Response(403, text="PRIVATE UPSTREAM PAYLOAD")
    if path == "campaigns/7/entities/102/inventory":
        if page == 1:
            rows = [
                {
                    "id": 501,
                    "entity_id": 102,
                    "item_id": 21,
                    "name": None,
                    "amount": "2",
                    "is_equipped": True,
                    "position": "Belt",
                    "is_private": False,
                },
                {
                    "id": 502,
                    "entity_id": 102,
                    "item_id": None,
                    "name": "Rope",
                    "amount": 1,
                    "is_equipped": False,
                    "position": "Pack",
                    "is_private": False,
                },
            ]
        else:
            rows = [
                {
                    "id": 503,
                    "entity_id": 102,
                    "item_id": 21,
                    "name": "Backup compass",
                    "amount": 1,
                    "is_equipped": False,
                    "position": "Pack",
                }
            ]
        return envelope(rows, page=page, last=2)
    if path == "campaigns/7/entities/103/relations":
        return envelope(
            [
                {
                    "id": 601,
                    "owner_id": 103,
                    "target_id": 101,
                    "relation": "Mentor",
                    "attitude": 25,
                },
                {
                    "id": 602,
                    "owner_id": 103,
                    "target_id": 101,
                    "relation": "Rival",
                    "attitude": -25,
                },
                {
                    "id": 603,
                    "owner_id": 102,
                    "target_id": 103,
                    "relation": "Ally",
                    "attitude": 50,
                },
                {
                    "id": 604,
                    "owner_id": 103,
                    "target_id": 999,
                    "relation": "Unknown",
                    "attitude": 0,
                },
            ]
        )
    if path == "campaigns/7/entities/101/posts":
        return envelope(
            [{"id": 701, "entity_id": 101, "name": "History", "entry": "Test post"}]
        )
    if path.startswith("campaigns/7/entities/") and path.count("/") == 4:
        if path.endswith("/image"):
            return httpx.Response(200, json={"image": None, "header": None})
        return envelope([])
    if path.startswith("campaigns/7/entities/"):
        entity_id = int(path.rsplit("/", 1)[1])
        if entity_id in NAMES:
            return envelope(entity(entity_id))
    return httpx.Response(404, text="PRIVATE UPSTREAM PAYLOAD")
