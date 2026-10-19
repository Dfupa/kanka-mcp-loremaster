"""Synthetic API for real opt-in server entry-point tests; no real transport."""

import json

import httpx


def handle(rehearsal, request):
    path = request.url.path
    if path == "/1.0/campaigns/7":
        rehearsal.requests.append((request.method, "campaigns/7"))
        return httpx.Response(
            200, json={"data": {"id": 7, "name": "Synthetic campaign"}}
        )
    parts = path.removeprefix("/1.0/").split("/")
    organisation_core = path == "/1.0/campaigns/7/organisations"
    item_core = path == "/1.0/campaigns/7/items"
    membership = (
        len(parts) == 5
        and parts[:3] == ["campaigns", "7", "organisations"]
        and parts[4] == "organisation_members"
    )
    if (organisation_core or item_core) and request.method == "POST" or membership:
        rehearsal.requests.append((request.method, path.removeprefix("/1.0/")))
        if membership:
            owner = next(
                (
                    uid
                    for uid, row in rehearsal.records.items()
                    if row["id"] == int(parts[3])
                    and row.get("entity_type") == "organisation"
                ),
                None,
            )
            if owner is None:
                return httpx.Response(404)
            rows = rehearsal.attachments.setdefault(owner, {}).setdefault("members", [])
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "data": rows,
                        "meta": {"last_page": 1, "current_page": 1, "per_page": 45},
                    },
                )
            rehearsal._active_key = f"member:{len(rows)}"
        else:
            rehearsal._active_key = "organisation" if organisation_core else "item"
        if request.method != "POST":
            return httpx.Response(405)
        if rehearsal.failure and rehearsal.failure[0] == rehearsal._active_key:
            return httpx.Response(
                rehearsal.failure[1], text="SYNTHETIC PRIVATE FAILURE"
            )
        body = json.loads(request.content)
        if organisation_core or item_core:
            uid = max(rehearsal.records) + 1
            created = {
                **body,
                "id": uid + 1000,
                "entity_id": uid,
                "entity_type": "organisation" if organisation_core else "item",
            }
            rehearsal.records[uid] = created
        else:
            created = {**body, "id": len(rows) + 1}
            rows.append(created)
        if rehearsal.lose_response == rehearsal._active_key:
            raise httpx.ReadTimeout("Synthetic lost response", request=request)
        return httpx.Response(201, json={"data": created})
    if request.method == "POST":
        if path.endswith("/characters"):
            rehearsal._active_key = "character"
        else:
            owner = int(path.split("/")[-2])
            kind = path.split("/")[-1]
            prefix = {
                "relations": "relation",
                "inventory": "inventory",
                "posts": "post",
            }[kind]
            rehearsal._active_key = (
                f"{prefix}:{len(rehearsal.attachments.get(owner, {}).get(kind, []))}"
            )
    response = rehearsal._handle(request)
    if response.status_code == 200 and request.method == "POST":
        payload = response.json()
        if path.endswith("/relations"):
            payload["data"] = [payload["data"]]
        return httpx.Response(201, json=payload)
    if response.status_code == 200 and request.method == "GET":
        payload = response.json()
        if path.endswith("/inventory"):
            for row in payload["data"]:
                row["amount"] = int(row["amount"])
        if path.endswith("/relations"):
            for row in payload["data"]:
                row.pop("two_way", None)
        return httpx.Response(200, json=payload)
    return response
