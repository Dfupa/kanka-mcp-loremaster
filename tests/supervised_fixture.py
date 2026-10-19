"""Synthetic API for real opt-in server entry-point tests; no real transport."""

import httpx


def handle(rehearsal, request):
    path = request.url.path
    if path == "/1.0/campaigns/7":
        rehearsal.requests.append((request.method, "campaigns/7"))
        return httpx.Response(
            200, json={"data": {"id": 7, "name": "Synthetic campaign"}}
        )
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
