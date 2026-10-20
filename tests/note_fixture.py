"""Invented session notes and a GET-only Kanka transport."""

import httpx

TEXT = (
    "Sesión 12 — 17/10/2026\r\n"
    "L = Lía; V = Vélar.\r\n"
    "L llego al Puente Gris.\r\n"
    "V dijo: «no pienso bolver, ni por mil monedas».\r\n"
    "V cree q el gremio robó la llave, pero no lo vio.\r\n"
    "Objetivo: Recuperar la llave.\r\n"
    "Antes tenemos que hablar con la barquera.\r\n"
    "Sesión 13 — 19/10/2026\r\n"
    "Mara nos enseño el paso.\r\n"
    "Recuperar la llave: Recogimos la llave.\r\n"
)


def anchor(block, excerpt, **kwargs):
    return {"block_id": f"b-{block}", "excerpt": excerpt, **kwargs}


def analysis():
    return {
        "sessions": [
            {
                "key": "s12",
                "label": "Session 12",
                "block_ids": [f"b-{i}" for i in range(1, 8)],
                "date_anchor": anchor(1, "17/10/2026"),
            },
            {
                "key": "s13",
                "label": "Session 13",
                "block_ids": [f"b-{i}" for i in range(8, 11)],
                "date_anchor": anchor(8, "19/10/2026"),
            },
        ],
        "analysed_blocks": [f"b-{i}" for i in range(1, 11)],
        "edits": [
            {"anchor": anchor(3, "llego"), "replacement": "llegó", "reason": "Accent"},
            {
                "anchor": anchor(9, "enseño"),
                "replacement": "enseñó",
                "reason": "Accent",
            },
        ],
        "mentions": [
            {
                "key": "lia",
                "session_key": "s12",
                "anchor": anchor(3, "L", after=" llego"),
                "name": "Lía",
                "entity_type": "character",
                "alias_anchor": anchor(2, "L = Lía; V = Vélar."),
            },
            {
                "key": "velar",
                "session_key": "s12",
                "anchor": anchor(4, "V", after=" dijo"),
                "name": "Vélar",
                "entity_type": "character",
                "alias_anchor": anchor(2, "L = Lía; V = Vélar."),
            },
            {
                "key": "q12",
                "session_key": "s12",
                "anchor": anchor(6, "Recuperar la llave"),
                "name": "Recuperar la llave",
                "entity_type": "quest",
                "sections": ["posts"],
            },
            {
                "key": "q13",
                "session_key": "s13",
                "anchor": anchor(10, "Recuperar la llave"),
                "name": "Recuperar la llave",
                "entity_type": "quest",
                "sections": ["posts"],
            },
            {
                "key": "mara",
                "session_key": "s13",
                "anchor": anchor(9, "Mara"),
                "name": "Mara",
                "entity_type": "character",
            },
        ],
        "claims": [
            {
                "key": "arrival",
                "session_key": "s12",
                "anchors": [anchor(3, "L llego al Puente Gris.")],
                "text": "Lía llegó al Puente Gris.",
                "subject": "lia",
            },
            {
                "key": "rumor",
                "session_key": "s12",
                "anchors": [
                    anchor(5, "V cree q el gremio robó la llave, pero no lo vio.")
                ],
                "text": "Vélar sospecha del gremio, sin haber visto el robo.",
                "mode": "rumor",
                "attribution": "velar",
                "polarity": "uncertain",
            },
            {
                "key": "goal",
                "session_key": "s12",
                "anchors": [anchor(7, "Antes tenemos que hablar con la barquera.")],
                "text": "Hablar con la barquera contribuye a recuperar la llave.",
                "mode": "plan",
            },
            {
                "key": "recovery",
                "session_key": "s13",
                "anchors": [anchor(10, "Recogimos la llave.")],
                "text": "El grupo informó que recogió la llave.",
                "mode": "reported",
            },
            {
                "key": "guide",
                "session_key": "s13",
                "anchors": [anchor(9, "Mara nos enseño el paso.")],
                "text": "Mara mostró el paso.",
                "subject": "mara",
            },
        ],
    }


def plan():
    return {
        "assessments": [],
        "operations": [
            {
                "key": "journal12",
                "action": "create_entity",
                "entity_type": "journal",
                "session_key": "s12",
                "claim_keys": ["arrival", "rumor", "goal"],
                "fields": {"name": "Session 12"},
                "session_text": True,
            },
            {
                "key": "post12",
                "action": "create_post",
                "entity_type": "post",
                "session_key": "s12",
                "claim_keys": ["rumor"],
                "target": {"kind": "mention", "key": "q12"},
                "fields": {"name": "2026-10-17 — Session 12"},
            },
            {
                "key": "dialogue12",
                "action": "create_post",
                "entity_type": "post",
                "session_key": "s12",
                "claim_keys": ["goal"],
                "target": {"kind": "mention", "key": "q12"},
                "fields": {"name": "2026-10-17 — Session 12"},
                "body": [
                    {
                        "kind": "quote",
                        "anchor": anchor(4, "«no pienso bolver, ni por mil monedas»"),
                    }
                ],
            },
            {
                "key": "child",
                "action": "create_entity",
                "entity_type": "quest",
                "session_key": "s12",
                "claim_keys": ["goal"],
                "fields": {"name": "Hablar con la barquera"},
                "references": [
                    {
                        "field": "parent_entity_id",
                        "target": {"kind": "mention", "key": "q12"},
                    }
                ],
            },
            {
                "key": "journal13",
                "action": "create_entity",
                "entity_type": "journal",
                "session_key": "s13",
                "claim_keys": ["recovery", "guide"],
                "fields": {"name": "Session 13"},
                "session_text": True,
            },
            {
                "key": "post13",
                "action": "create_post",
                "entity_type": "post",
                "session_key": "s13",
                "claim_keys": ["recovery"],
                "target": {"kind": "mention", "key": "q13"},
                "fields": {"name": "2026-10-19 — Session 13"},
            },
            {
                "key": "new-mara",
                "action": "create_entity",
                "entity_type": "character",
                "session_key": "s13",
                "claim_keys": ["guide"],
                "fields": {"name": "Mara"},
            },
        ],
    }


class NoteAPI:
    def __init__(self):
        self.requests = []
        self.is_private = True
        self.duplicates = False
        self.fail_search = False
        self.long_posts = False
        self.cores = {
            101: (11, "character", "Lía"),
            102: (12, "character", "Vélar"),
            201: (21, "quest", "Recuperar la llave"),
        }

    def generic(self, uid):
        child, kind, name = self.cores[uid]
        return {
            "id": uid,
            "child_id": child,
            "type": kind,
            "name": name,
            "campaign_id": 7,
            "is_private": self.is_private,
        }

    def __call__(self, request):
        if request.method != "GET":
            raise AssertionError("The note workflow must never send writes")
        self.requests.append(request)
        path = request.url.path.removeprefix("/1.0/")
        page = int(request.url.params.get("page", 1))

        def response(data, last=1):
            return httpx.Response(
                200,
                json={"data": data, "meta": {"current_page": page, "last_page": last}},
            )

        if path == "profile":
            return response({"rate_limit": 1000000, "default_pagination": 45})
        if path == "campaigns/7/entities":
            if self.fail_search:
                return httpx.Response(403, text="PRIVATE RESPONSE MUST NOT LEAK")
            query = request.url.params["name"].casefold()
            rows = [
                self.generic(uid)
                for uid, (_, _, name) in self.cores.items()
                if query in name.casefold()
            ]
            if self.duplicates and query == "vélar":
                rows.append({**self.generic(102), "id": 103, "child_id": 13})
            return response(rows)
        for uid, (child, kind, name) in self.cores.items():
            if path == f"campaigns/7/entities/{uid}":
                return response(self.generic(uid))
            module = "characters" if kind == "character" else "quests"
            if path == f"campaigns/7/{module}/{child}":
                return response(
                    {
                        "id": child,
                        "entity_id": uid,
                        "name": name,
                        "entry": "<p>Established account.</p>",
                        "is_private": self.is_private,
                    }
                )
            if path == f"campaigns/7/entities/{uid}/posts":
                return response([], last=2 if self.long_posts else 1)
        raise AssertionError(f"Unexpected GET endpoint: {path}")
