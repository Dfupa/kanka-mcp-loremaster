"""Synthetic edit API; transport injection is mandatory in every caller."""

import json
from copy import deepcopy

import httpx


class EditAPI:
    def __init__(self):
        self.requests = []
        self.failure = None
        self.lose_response = False
        self.response_hook = None
        self.records = {
            101: {
                "id": 11,
                "entity_id": 101,
                "name": "Élan",
                "entry": "<p>Old lore.</p>",
                "entity_type": "character",
                "age": "28",
                "sex": "Male",
                "races": [21],
                "locations": [31],
                "tags": [41],
                "is_private": False,
                "updated_at": "version-1",
                "title": "Scout",
            },
            102: {
                "id": 12,
                "entity_id": 102,
                "name": "Mentor",
                "entry": "Mentor",
                "entity_type": "character",
                "is_private": False,
            },
        }
        for uid, child, kind, name in [
            (201, 21, "race", "Human"),
            (202, 22, "race", "Elf"),
            (301, 31, "location", "Home"),
            (302, 32, "location", "Harbour"),
            (401, 41, "tag", "Scout"),
            (402, 42, "tag", "Explorer"),
            (501, 51, "item", "Compass"),
            (502, 52, "item", "Map"),
        ]:
            self.records[uid] = {
                "id": child,
                "entity_id": uid,
                "entity_type": kind,
                "name": name,
                "entry": name,
                "is_private": False,
            }
        self.attachments = {
            "posts": [
                {
                    "id": 61,
                    "entity_id": 101,
                    "name": "First post",
                    "entry": "<p>First.</p>",
                    "visibility_id": 1,
                    "updated_at": "post-v1",
                },
                {
                    "id": 62,
                    "entity_id": 101,
                    "name": "Second post",
                    "entry": "<p>Second.</p>",
                    "visibility_id": 3,
                },
            ],
            "inventory": [
                {
                    "id": 71,
                    "entity_id": 101,
                    "item_id": 51,
                    "name": None,
                    "amount": 2,
                    "is_equipped": True,
                    "position": "Belt",
                    "visibility_id": 1,
                },
                {
                    "id": 72,
                    "entity_id": 101,
                    "item_id": 52,
                    "name": None,
                    "amount": 1,
                    "is_equipped": False,
                    "position": "Pack",
                    "visibility_id": 1,
                },
            ],
            "relations": [
                {
                    "id": 81,
                    "owner_id": 101,
                    "target_id": 102,
                    "relation": "Mentor",
                    "attitude": 25,
                    "is_pinned": False,
                    "colour": "#123456",
                    "visibility_id": 1,
                },
                {
                    "id": 82,
                    "owner_id": 102,
                    "target_id": 101,
                    "relation": "Apprentice",
                    "attitude": 25,
                    "is_pinned": False,
                    "visibility_id": 1,
                },
            ],
            "entity_tags": [{"id": 91, "tag_id": 41}],
        }

    @staticmethod
    def page(rows, page):
        last = max(1, len(rows))
        return {
            "data": deepcopy(rows[page - 1 : page]),
            "meta": {"last_page": last, "current_page": page, "per_page": 1},
        }

    def handle(self, request):
        path = request.url.path.removeprefix("/1.0/")
        self.requests.append(
            (
                request.method,
                path,
                json.loads(request.content) if request.content else None,
            )
        )
        parts = path.split("/")
        if request.method not in ("GET", "PATCH"):
            raise AssertionError("Only GET/PATCH are permitted in edit fixtures")
        if path == "profile":
            response = httpx.Response(
                200, json={"data": {"rate_limit": 10**9, "default_pagination": 1}}
            )
        elif path == "campaigns/7":
            response = httpx.Response(
                200, json={"data": {"id": 7, "name": "Synthetic"}}
            )
        elif parts[:2] != ["campaigns", "7"]:
            response = httpx.Response(403)
        elif parts[2:] == ["entities"]:
            rows = []
            for uid, record in self.records.items():
                if (
                    request.url.params.get("name", "").casefold()
                    in record["name"].casefold()
                ):
                    rows.append(self.metadata(uid))
            response = httpx.Response(
                200, json=self.page(rows, int(request.url.params.get("page", 1)))
            )
        elif len(parts) == 4 and parts[2] == "entities":
            uid = int(parts[3])
            response = (
                httpx.Response(200, json={"data": self.metadata(uid)})
                if uid in self.records
                else httpx.Response(404)
            )
        elif len(parts) == 4:
            matches = [
                record
                for record in self.records.values()
                if record["id"] == int(parts[3])
            ]
            if not matches:
                response = httpx.Response(404)
            else:
                record = matches[0]
                response = (
                    self.patch(request, record)
                    if request.method == "PATCH"
                    else httpx.Response(200, json={"data": deepcopy(record)})
                )
        elif (
            len(parts) in (5, 6)
            and parts[2:4] == ["entities", "101"]
            and parts[4] in self.attachments
        ):
            rows = self.attachments[parts[4]]
            if len(parts) == 5:
                response = httpx.Response(
                    200, json=self.page(rows, int(request.url.params.get("page", 1)))
                )
            else:
                matches = [row for row in rows if row["id"] == int(parts[5])]
                if not matches:
                    response = httpx.Response(404)
                else:
                    response = (
                        self.patch(request, matches[0])
                        if request.method == "PATCH"
                        else httpx.Response(200, json={"data": deepcopy(matches[0])})
                    )
        else:
            response = httpx.Response(404)
        return self.response_hook(request, response) if self.response_hook else response

    def metadata(self, uid):
        row = self.records[uid]
        return {
            "id": uid,
            "campaign_id": 7,
            "child_id": row["id"],
            "type": row["entity_type"],
            "name": row["name"],
        }

    def patch(self, request, record):
        if self.failure:
            return httpx.Response(self.failure, text="PRIVATE UPSTREAM BODY")
        body = json.loads(request.content)
        if "/inventory/" in request.url.path and (
            "entity_id" not in body
            or "amount" not in body
            or not (body.get("item_id") or body.get("name"))
        ):
            return httpx.Response(422, json={"message": "Required inventory fields"})
        old_tag = record.get("tag_id")
        record.update(body)
        if old_tag is not None and "tag_id" in body:
            self.records[101]["tags"] = sorted(
                body["tag_id"] if uid == old_tag else uid
                for uid in self.records[101]["tags"]
            )
        if "tags" in body:
            self.attachments["entity_tags"] = [
                {"id": 91 + index, "tag_id": uid}
                for index, uid in enumerate(body["tags"])
            ]
        if "updated_at" in record:
            record["updated_at"] = "version-2"
        if self.lose_response:
            raise httpx.ReadTimeout("Synthetic lost PATCH response", request=request)
        return httpx.Response(200, json={"data": deepcopy(record)})
