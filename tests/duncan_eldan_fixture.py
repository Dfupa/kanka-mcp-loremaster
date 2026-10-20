"""Captured host analysis and synthetic API for the Duncan/Éldan editorial trial.

The authored replacements and claim selections below are test inputs, not a
general language parser. The evaluator's Markdown is never an ingestion source.
"""

import re
from pathlib import Path

import httpx

from loremaster.note_models import NoteAnalysis, NotePlan
from loremaster.note_text import source_blocks

SOURCE = Path(__file__).parent / "fixtures/session_notes/duncan_eldan_noisy.txt"
TEXT = SOURCE.read_text(encoding="utf-8")
BLOCKS = source_blocks(TEXT)
SPLIT = next(i for i, b in enumerate(BLOCKS) if b["text"].startswith("SESION 02"))


def anchor(excerpt, session="s1", *, before="", after=""):
    """Select unique source wording, keeping character counting out of host input."""
    candidates = BLOCKS[:SPLIT] if session == "s1" else BLOCKS[SPLIT:]
    matches = [
        b
        for b in candidates
        if excerpt in b["text"] and before + excerpt + after in b["text"]
    ]
    if len(matches) != 1:
        raise ValueError(f"Ambiguous authored fixture anchor: {excerpt}")
    return {
        "block_id": matches[0]["id"],
        "excerpt": excerpt,
        "before": before,
        "after": after,
    }


def host_analysis():
    """Return source-backed host judgments plus corrections blocked by quote guards."""
    corrections = {
        "SINTETICOS": "SINTÉTICOS",
        "SESION": "SESIÓN",
        "correcion": "corrección",
        "huerfano": "huérfano",
        "q": "que",
        "tambien": "también",
        "mia": "mía",
        "quedo": "quedó",
        "invitacion": "invitación",
        "afiliacion": "afiliación",
        "despues": "después",
        "hablara": "hablará",
        "ponia": "ponía",
        "mansion": "mansión",
        "haciendose": "haciéndose",
        "ocurrio": "ocurrió",
        "vacia": "vacía",
        "dejo": "dejó",
        "robo": "robó",
        "ataco": "atacó",
        "descubrio": "descubrió",
        "tenia": "tenía",
        "giro": "giró",
        "abrio": "abrió",
        "detras": "detrás",
        "pq": "porque",
        "segun": "según",
        "adopto": "adoptó",
        "aparecio": "apareció",
        "ganzua": "ganzúa",
        "Sono": "Sonó",
        "campanilla": "campanilla",
        "tuberias": "tuberías",
        "Habia": "Había",
        "guardian": "guardián",
        "tiro": "tiró",
        "calcetin": "calcetín",
        "ronco": "roncó",
        "confirmo": "confirmó",
        "penso": "pensó",
        "levanto": "levantó",
        "guardo": "guardó",
        "sujeto": "sujetó",
        "paso": "pasó",
        "ningun": "ningún",
        "sesion": "sesión",
        "Proximo": "Próximo",
        "todavia": "todavía",
        "continuacion": "continuación",
        "ficcion": "ficción",
        "dias": "días",
        "cumpleaños": "cumpleaños",
        "fantastica": "fantástica",
        "aun": "aún",
        "saco": "sacó",
        "brujula": "brújula",
        "señalo": "señaló",
        "podia": "podía",
        "brujulas": "brújulas",
        "valvula": "válvula",
        "portillo": "portillo",
        "lavanderia": "lavandería",
        "seguia": "seguía",
        "sabana": "sábana",
        "escondio": "escondió",
        "empujo": "empujó",
        "callejon": "callejón",
        "enseño": "enseñó",
        "volvio": "volvió",
        "recuperacion": "recuperación",
        "mision": "misión",
        "rapido": "rápido",
        "dueño": "dueño",
    }
    edits, pending = [], []
    for block in BLOCKS:
        if block["text"].startswith(("D: ", "E: ")):
            continue
        for match in re.finditer(r"\b\w+\b", block["text"]):
            word = match.group()
            replacement = corrections.get(word, word)
            if replacement == word:
                continue
            edit = {
                "anchor": {
                    "block_id": block["id"],
                    "excerpt": word,
                    "before": block["text"][max(0, match.start() - 24) : match.start()],
                    "after": block["text"][match.end() : match.end() + 24],
                },
                "replacement": replacement,
                "reason": "Captured host spelling/accent correction",
            }
            if any(
                match.start() < end and match.end() > start
                for start, end in block["protected"]
            ):
                pending.append(
                    {
                        **edit,
                        "reason": "Narrative label protected by conservative "
                        "speaker guard",
                    }
                )
            else:
                edits.append(edit)
    mentions = []

    def mention(key, name, kind, excerpt, session="s1", alias=None, sections=()):
        selectors = {
            "duncan1": ("Dun", "", " y E entraron"),
            "eldan1": ("E", "En la sala ", " descubrio"),
            "thieves": ("GL", "miembros del ", ", el Gremio"),
            "explorers": ("GE", "del ", " / Gremio"),
            "bone": ("Lord Bone", "las mansiones de ", ", sacar"),
            "estate": ("mansiones de Lord Bone", "las ", ", sacar"),
        }
        selected, before, after = selectors.get(key, (excerpt, "", ""))
        value = {
            "key": key,
            "session_key": session,
            "anchor": anchor(selected, session, before=before, after=after),
            "name": name,
            "entity_type": kind,
            "sections": list(sections),
        }
        if alias:
            value["alias_anchor"] = anchor(alias, session)
        mentions.append(value)

    mention(
        "duncan1",
        "Duncan",
        "character",
        "Dun",
        alias="Dun = Duncan tambien.",
        sections=("posts", "inventory"),
    )
    mention(
        "eldan1",
        "Éldan",
        "character",
        "E",
        alias="E = Éldan",
        sections=("posts", "inventory"),
    )
    # Full names are explicitly present in session two; no cross-session alias override.
    mention("duncan2", "Duncan", "character", "Duncan giro la rueda", "s2")
    mentions[-1]["anchor"] = anchor("Duncan", "s2", after=" giro la rueda")
    mention("eldan2", "Éldan", "character", "Éldan vigilaba el pasillo", "s2")
    mentions[-1]["anchor"] = anchor("Éldan", "s2", after=" vigilaba el pasillo")
    mention("quest1", "El escape", "quest", "El escape.", sections=("posts",))
    mentions[-1]["anchor"] = anchor(
        "El escape", before="El encargo se llama ", after="."
    )
    mention(
        "quest2",
        "El escape",
        "quest",
        "El escape, continuacion",
        "s2",
        sections=("posts",),
    )
    mentions[-1]["anchor"] = anchor("El escape", "s2", after=", continuacion")
    mention(
        "thieves",
        "Gremio de Ladrones",
        "organisation",
        "GL",
        alias="GL = Gremio de Ladrones",
    )
    mention(
        "explorers",
        "Gremio de Exploradores",
        "organisation",
        "GE",
        alias="GE = Gremio de Exploradores",
    )
    mention("torno", "Torno", "family", "familia Torno")
    mentions[-1]["anchor"] = anchor("Torno", before="familia ", after=".")
    mention("bone", "Lord Bone", "character", "las mansiones de Lord Bone")
    mentions[-1]["anchor"] = anchor(
        "Lord Bone", before="las mansiones de ", after=", sacar"
    )
    mention(
        "estate", "Mansiones de Lord Bone", "location", "las mansiones de Lord Bone"
    )
    mentions[-1]["anchor"] = anchor(
        "mansiones de Lord Bone", before="las ", after=", sacar"
    )
    mention(
        "maze",
        "Laberinto oculto de las mansiones de Lord Bone",
        "location",
        "Era el laberinto oculto de las mansiones de Lord Bone.",
    )
    mentions[-1]["anchor"] = anchor("laberinto oculto de las mansiones de Lord Bone")
    mention("gem", "Gema Sagrada", "item", "sacar la Gema Sagrada")
    mentions[-1]["anchor"] = anchor(
        "Gema Sagrada", before="sacar la ", after=" y salir"
    )
    claims = []

    def claim(key, excerpt, text, session="s1", **kwargs):
        claims.append(
            {
                "key": key,
                "session_key": session,
                "anchors": [anchor(excerpt, session)],
                "text": text,
                **kwargs,
            }
        )

    claim(
        "age",
        "Duncan tiene 26 años",
        "La corrección explícita del DJ fija la edad de Duncan en 26 años.",
        subject="duncan1",
    )
    claim(
        "orphan",
        "es huerfano",
        "Duncan es huérfano; no se conocen sus padres.",
        subject="duncan1",
    )
    claim(
        "not-torno",
        "NO pertenece a la familia Torno",
        "Duncan no pertenece a la familia Torno.",
        subject="duncan1",
        polarity="negative",
    )
    claim(
        "guild-duncan",
        "D y E son miembros del GL, el Gremio de Ladrones.",
        "Duncan pertenece al Gremio de Ladrones.",
        subject="duncan1",
    )
    claim(
        "guild-eldan",
        "D y E son miembros del GL, el Gremio de Ladrones.",
        "Éldan pertenece al Gremio de Ladrones.",
        subject="eldan1",
    )
    claim(
        "not-explorers",
        "NO son miembros del GE / Gremio de Exploradores.",
        "Ninguno pertenece al Gremio de Exploradores; la invitación era una tapadera.",
        polarity="negative",
    )
    claim(
        "estate",
        "las mansiones son un solo recinto de LB.",
        "Lord Bone posee un único recinto de mansiones con un laberinto oculto debajo.",
        subject="bone",
    )
    claim(
        "maze",
        "Hay UN laberinto oculto bajo el recinto",
        "Un solo laberinto oculto se encuentra bajo las mansiones.",
    )
    claim(
        "gem",
        "Sacred Gem, es la misma gema, no dos piedras.",
        "Gema Sagrada y Sacred Gem designan el mismo objeto.",
    )
    claim(
        "lock-failure",
        "D quiso forzar la del sol con la ganzua pero FALLO, no se abrio.",
        "Duncan falla al forzar la puerta del sol; no se abre.",
        mode="failed",
        subject="duncan1",
        polarity="negative",
    )
    claim(
        "sleeping-guard",
        "No lo matamos, no luchamos con el",
        "El guardián no fue atacado ni murió.",
        polarity="negative",
    )
    claim(
        "retrieval",
        "E levanto la Gema Sagrada y la guardo en su bolsa",
        "Éldan recupera la Gema Sagrada y la guarda en su bolsa.",
        subject="eldan1",
    )
    claim(
        "trapped",
        "atrapados dentro del laberinto, con la gema ya recuperada "
        "pero SIN haber escapado.",
        "La sesión 01 termina con la gema recuperada y ambos todavía atrapados.",
    )
    claim(
        "exit-goal",
        "Proximo objetivo: encontrar salida secundaria siguiendo las tuberias.",
        "El siguiente objetivo es encontrar una salida secundaria.",
        mode="plan",
    )
    claim(
        "same-night",
        "Retomamos la MISMA noche dentro de la ficcion, unos minutos despues.",
        "La sesión 02 continúa minutos después, en la misma noche ficticia.",
        "s2",
    )
    claim(
        "vampire-rumor",
        "E dijo q seguramente LB era vampiro pq tenia muchas cortinas.",
        "Éldan sospecha que Lord Bone sea vampiro; no está confirmado.",
        "s2",
        mode="rumor",
        polarity="uncertain",
        attribution="eldan2",
    )
    claim(
        "compass-joke",
        "Mi brújula tiene cinco nortes. Edición de lujo.",
        "Duncan bromea sobre cinco nortes; no cambia ninguna cantidad de brújulas.",
        "s2",
        mode="dialogue",
        attribution="duncan2",
    )
    claim(
        "secondary-exit",
        "Se abrio un portillo de mantenimiento hacia la lavanderia de arriba",
        "Al girar Duncan la válvula se abre la salida de mantenimiento "
        "hacia la lavandería.",
        "s2",
        subject="duncan2",
    )
    claim(
        "false-ghost",
        "Era una sabana encima de una escoba, no un fantasma.",
        "La figura era una sábana sobre una escoba, no un fantasma.",
        "s2",
        polarity="negative",
    )
    claim(
        "escape",
        "Los DOS escaparon del recinto con la gema intacta.",
        "Ambos escapan del recinto con la gema intacta en la sesión 02.",
        "s2",
    )
    claim(
        "possession",
        "Éldan enseño a Duncan la Gema Sagrada y volvio a guardarla en SU bolsa.",
        "Éldan conserva la única gema intacta en su bolsa al cierre.",
        "s2",
        subject="eldan2",
    )
    claim(
        "no-delivery",
        "no entregamos la gema a nadie.",
        "No entregaron ni vendieron la gema y no cobraron recompensa.",
        "s2",
        polarity="negative",
    )
    claim(
        "future-delivery",
        "Posible siguiente encargo: devolverla al GL, pero eso solo es una propuesta",
        "Devolver la gema al gremio es una sugerencia futura, no una misión aceptada.",
        "s2",
        mode="hypothetical",
    )
    data = {
        "sessions": [
            {
                "key": "s1",
                "label": "El escape — sesión 01",
                "block_ids": [b["id"] for b in BLOCKS[:SPLIT]],
                "date_anchor": anchor("17/10/2026"),
            },
            {
                "key": "s2",
                "label": "El escape — sesión 02",
                "block_ids": [b["id"] for b in BLOCKS[SPLIT:]],
                "date_anchor": anchor("19/10/2026", "s2"),
            },
        ],
        "analysed_blocks": [b["id"] for b in BLOCKS],
        "edits": edits,
        "mentions": mentions,
        "claims": claims,
    }
    return NoteAnalysis.model_validate(data), pending


def host_plan(row):
    """Produce a cautious evidence-aware plan; contradictions remain blocked."""
    operations, assessments = [], []
    state = row["state"]

    def operation(key, action, kind, session, claims, **kwargs):
        operations.append(
            {
                "key": key,
                "action": action,
                "entity_type": kind,
                "session_key": session,
                "claim_keys": claims,
                **kwargs,
            }
        )

    def ref(key):
        return {"kind": "mention", "key": key}

    for session, date, claims in [
        (
            "s1",
            "2026-10-17",
            ["retrieval", "trapped", "lock-failure", "sleeping-guard"],
        ),
        (
            "s2",
            "2026-10-19",
            ["secondary-exit", "escape", "possession", "no-delivery", "vampire-rumor"],
        ),
    ]:
        operation(
            "journal-" + session,
            "create_entity",
            "journal",
            session,
            claims,
            fields={"name": f"[LoreMaster test] El escape — {date}"},
            session_text=True,
        )
        operation(
            "quest-post-" + session,
            "create_post",
            "post",
            session,
            claims,
            target=ref("quest1" if session == "s1" else "quest2"),
            fields={"name": f"[LoreMaster test] Sesión {date}"},
        )
    operation(
        "duncan-age",
        "edit_entity",
        "character",
        "s1",
        ["age"],
        target=ref("duncan1"),
        fields={"age": "26"},
    )
    operation(
        "duncan-background",
        "create_post",
        "post",
        "s1",
        ["age", "orphan", "not-torno", "guild-duncan"],
        target=ref("duncan1"),
        fields={"name": "[LoreMaster test] Aclaraciones — 2026-10-17"},
    )
    for key, kind, claim_key in [
        ("bone", "character", "estate"),
        ("estate", "location", "estate"),
        ("maze", "location", "maze"),
        ("gem", "item", "gem"),
    ]:
        if state["lookups"][key]["status"] != "resolved":
            name = next(
                m["name"] for m in state["analysis"]["mentions"] if m["key"] == key
            )
            operation(
                "new-" + key,
                "create_entity",
                kind,
                "s1",
                [claim_key],
                fields={"name": name},
            )
    for key, session, claims, target in [
        (
            "guild-memberships",
            "s1",
            ["guild-duncan", "guild-eldan", "not-explorers"],
            "thieves",
        ),
        ("family-correction", "s1", ["not-torno"], "duncan1"),
        ("gem-inventory", "s2", ["possession", "no-delivery"], "eldan2"),
        ("quest-status", "s2", ["escape", "no-delivery"], "quest2"),
        ("maze-hierarchy", "s1", ["maze"], "estate"),
    ]:
        operation(
            key,
            "deferred",
            "organisation"
            if key == "guild-memberships"
            else "character"
            if key == "family-correction"
            else "inventory"
            if key == "gem-inventory"
            else "quest"
            if key == "quest-status"
            else "location",
            session,
            claims,
            target=ref(target),
        )
    duncan = state["lookups"]["duncan1"]
    if duncan["status"] == "resolved" and duncan["core"]["details"].get("age") != "26":
        uid = duncan["candidate"]["entity_id"]
        evidence = next(
            e
            for e in state["evidence"].values()
            if e["entity_id"] == uid and e["section"] == "core"
        )
        assessments.append(
            {
                "claim_key": "age",
                "state": "contradiction",
                "explanation": "La fuente corrige la edad a 26; el registro actual "
                "discrepa. Requiere revisión explícita.",
                "citations": [
                    {"evidence_id": evidence["key"], "excerpt": evidence["text"]}
                ],
            }
        )
    return NotePlan.model_validate(
        {"assessments": assessments, "operations": operations}
    )


class NarrativeAPI:
    """Invented, GET-only canonical records; no real campaign IDs or live data."""

    def __init__(self, case="conflicting"):
        self.case, self.requests = case, []
        self.cores = {
            101: (11, "character", "Duncan"),
            102: (12, "character", "Éldan"),
            201: (21, "quest", "El escape"),
            301: (31, "family", "Torno"),
            401: (41, "organisation", "Gremio de Ladrones"),
            402: (42, "organisation", "Gremio de Exploradores"),
        }
        if case == "matching":
            self.cores.update(
                {
                    501: (51, "character", "Lord Bone"),
                    601: (61, "location", "Mansiones de Lord Bone"),
                    602: (
                        62,
                        "location",
                        "Laberinto oculto de las mansiones de Lord Bone",
                    ),
                    701: (71, "item", "Gema Sagrada"),
                }
            )
        if case == "duplicate":
            self.cores[103] = (13, "character", "Duncan")

    def generic(self, uid):
        child, kind, name = self.cores[uid]
        return {
            "id": uid,
            "child_id": child,
            "type": kind,
            "name": name,
            "campaign_id": 7,
            "is_private": self.case != "public",
        }

    def __call__(self, request):
        if request.method != "GET":
            raise AssertionError("The editorial trial forbids all HTTP writes")
        self.requests.append(request)
        path = request.url.path.removeprefix("/1.0/")
        page = int(request.url.params.get("page", 1))

        def response(data):
            return httpx.Response(
                200, json={"data": data, "meta": {"current_page": page, "last_page": 1}}
            )

        if path == "profile":
            return response({"rate_limit": 1000000, "default_pagination": 45})
        if path == "campaigns/7":
            return response({"id": 7, "name": "Invented editorial-test campaign"})
        if path == "campaigns/7/entities":
            name = request.url.params["name"].casefold()
            if self.case == "denied" and name == "gremio de ladrones":
                return httpx.Response(403, text="PRIVATE ERROR PAYLOAD")
            return response(
                [
                    self.generic(uid)
                    for uid, (_, _, label) in self.cores.items()
                    if name in label.casefold()
                ]
            )
        for uid, (child, kind, name) in self.cores.items():
            if path == f"campaigns/7/entities/{uid}":
                return response(self.generic(uid))
            module = {
                "character": "characters",
                "quest": "quests",
                "family": "families",
                "organisation": "organisations",
                "location": "locations",
                "item": "items",
            }[kind]
            if path == f"campaigns/7/{module}/{child}":
                details = {
                    "id": child,
                    "entity_id": uid,
                    "name": name,
                    "entry": "<p>Established synthetic record.</p>",
                    "is_private": self.case != "public",
                }
                if uid == 101:
                    details.update(
                        age="26" if self.case == "matching" else "45",
                        families=[] if self.case == "matching" else [31],
                    )
                return response(details)
            for section in ("posts", "inventory", "relations"):
                if path == f"campaigns/7/entities/{uid}/{section}":
                    return response([])
            if (
                kind == "organisation"
                and path == f"campaigns/7/organisations/{child}/organisation_members"
            ):
                correct = self.case == "matching"
                member = uid == (401 if correct else 402)
                return response(
                    [
                        {
                            "id": uid * 10 + i,
                            "organisation_id": child,
                            "character_id": cid,
                        }
                        for i, cid in enumerate((11, 12))
                    ]
                    if member
                    else []
                )
        raise AssertionError(f"Unexpected synthetic GET: {path}")
