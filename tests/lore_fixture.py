"""Synthetic family/quest/map/journal/note API; no real network transport."""

import json
from copy import deepcopy

import httpx
from edit_fixture import EditAPI

KINDS = {
    "family": "families",
    "quest": "quests",
    "map": "maps",
    "journal": "journals",
    "note": "notes",
}
TARGETS = {kind: (601 + index, 61 + index) for index, kind in enumerate(KINDS)}


def core_fields(kind):
    """Return documented baseline fields, including untouched module metadata."""
    fields = {"type": "Original", "tags": [41], "parent_id": 900, "status_id": 2}
    if kind in ("family", "quest", "journal"):
        fields["locations"] = [31]
    if kind == "family":
        fields["members"] = ["11"]
    elif kind == "quest":
        fields.update(instigator_id=101, elements=[{"id": 8, "entity_id": 102}])
    elif kind == "journal":
        fields.update(
            author_id=101,
            date="Year 12, Frostfall 3",
            calendar_id=77,
            calendar_year=12,
            calendar_month=8,
            calendar_day=3,
            calendar_event_length=1,
        )
    elif kind == "map":
        fields.update(
            location_id=31,
            is_real=False,
            height=400,
            width=800,
            grid=0,
            min_zoom=-1,
            max_zoom=10,
            initial_zoom=0,
            center_x=1.5,
            center_y=2.5,
            center_marker_id=9,
            image="synthetic-map.png",
            has_custom_image=True,
            layers=[{"id": 1, "name": "Terrain"}],
            groups=[{"id": 2, "name": "Places"}],
        )
    elif kind == "note":
        fields["is_pinned"] = 0
    return fields


class LoreAPI(EditAPI):
    """Support core POST/PATCH plus strict module routing and paginated reads."""

    def __init__(self):
        super().__init__()
        self.creation_failure = None
        self.lose_creation_response = False
        self.post_records = {}
        for kind, (uid, child) in TARGETS.items():
            self.records[uid] = {
                **core_fields(kind),
                "id": child,
                "entity_id": uid,
                "entity_type": kind,
                "name": "Existing " + kind,
                "entry": "<p>Original lore.</p>",
                "is_private": True,
                "updated_at": "version-1",
            }

    def metadata(self, uid):
        record = super().metadata(uid)
        if "parent_id" in self.records[uid]:
            record["parent_id"] = self.records[uid]["parent_id"]
        return record

    def handle(self, request):
        parts = request.url.path.removeprefix("/1.0/").split("/")
        if (
            len(parts) in (5, 6)
            and parts[:2] == ["campaigns", "7"]
            and parts[2] == "entities"
            and parts[4] == "posts"
        ):
            uid = int(parts[3])
            if uid not in self.records:
                return httpx.Response(404)
            if request.method == "POST" or uid in self.post_records or uid != 101:
                rows = self.post_records.setdefault(
                    uid, self.attachments["posts"] if uid == 101 else []
                )
                self.requests.append(
                    (
                        request.method,
                        "/".join(parts),
                        json.loads(request.content) if request.content else None,
                    )
                )
                if request.method == "POST" and len(parts) == 5:
                    if self.creation_failure:
                        return httpx.Response(self.creation_failure)
                    body = json.loads(request.content)
                    if body.get("entity_id") != uid or not body.get("name"):
                        return httpx.Response(422)
                    record = {
                        **body,
                        "id": max(
                            [2000]
                            + [
                                row["id"]
                                for collection in self.post_records.values()
                                for row in collection
                            ]
                        )
                        + 1,
                        "updated_at": "post-v1",
                    }
                    rows.append(record)
                    if self.lose_creation_response:
                        raise httpx.ReadTimeout("Synthetic lost POST", request=request)
                    response = httpx.Response(201, json={"data": deepcopy(record)})
                elif len(parts) == 5 and request.method == "GET":
                    response = httpx.Response(
                        200,
                        json=self.page(rows, int(request.url.params.get("page", 1))),
                    )
                else:
                    matches = [row for row in rows if row["id"] == int(parts[5])]
                    if len(matches) != 1:
                        return httpx.Response(404)
                    response = (
                        self.patch(request, matches[0])
                        if request.method == "PATCH"
                        else httpx.Response(200, json={"data": deepcopy(matches[0])})
                    )
                return (
                    self.response_hook(request, response)
                    if self.response_hook
                    else response
                )
        kind = next(
            (
                kind
                for kind, endpoint in (KINDS | {"location": "locations"}).items()
                if len(parts) >= 3 and parts[2] == endpoint
            ),
            None,
        )
        if request.method == "POST":
            if parts[:2] != ["campaigns", "7"] or len(parts) != 3 or kind is None:
                raise AssertionError("Unexpected creation endpoint")
            body = json.loads(request.content)
            self.requests.append(("POST", "/".join(parts), body))
            if self.creation_failure:
                return httpx.Response(self.creation_failure, text="PRIVATE FAILURE")
            allowed = {"name", "entry", "type", "tags", "is_private"}
            if kind == "location":
                allowed.add("title")
            if kind in ("family", "quest", "journal"):
                allowed.add("locations")
            if kind == "quest":
                allowed.add("instigator_id")
            if kind in ("quest", "note"):
                allowed.add("parent_id")
            if kind == "map":
                allowed.update({"location_id", "is_real"})
            if kind == "journal":
                allowed.update({"author_id", "date"})
            if set(body) - allowed or not body.get("name"):
                return httpx.Response(422)
            uid = max(self.records) + 1
            row = {
                "id": uid + 1000,
                "entity_id": uid,
                "entity_type": kind,
                "tags": [],
                "updated_at": "version-1",
                **body,
            }
            self.records[uid] = row
            if self.lose_creation_response:
                raise httpx.ReadTimeout("Synthetic lost POST response", request=request)
            return httpx.Response(201, json={"data": deepcopy(row)})
        if len(parts) == 4 and parts[2] != "entities":
            matches = [
                row for row in self.records.values() if row["id"] == int(parts[3])
            ]
            if (
                matches
                and KINDS.get(
                    matches[0]["entity_type"], matches[0]["entity_type"] + "s"
                )
                != parts[2]
            ):
                raise AssertionError("Wrong module endpoint")
        return super().handle(request)
