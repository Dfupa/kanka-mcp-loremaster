"""Compose direct entity data, with independent provenance and resumable sections."""

from typing import Any, Literal

from loremaster.kanka import (
    API_BASE_URL,
    ENTITY_COLLECTIONS,
    ENTITY_ENDPOINTS,
    MAX_CAMPAIGN_PAGES,
    KankaClient,
    KankaError,
)

EntitySection = Literal[
    "posts",
    "inventory",
    "relations",
    "attributes",
    "reminders",
    "assets",
    "abilities",
    "tags",
    "mentions",
    "permissions",
    "images",
]
SECTIONS = ("posts", "inventory", "relations", *ENTITY_COLLECTIONS, "images")


async def get_entity_full(
    client: KankaClient,
    campaign_id: int,
    entity_id: int,
    *,
    sections: list[EntitySection] | None = None,
    section_pages: dict[EntitySection, int] | None = None,
) -> dict[str, Any]:
    """Read core fields and all requested direct attachments within local budgets.

    None selects all supported sections; [] selects core only. Continuations
    re-read core and start each selected section at section_pages[name] (default
    1). No recursive graph expansion, binary downloads or date conversion occurs.
    A continuation is not a complete snapshot: callers must combine section pages
    and account for changes between requests. Authentication/schema failures fail
    closed; 403/404 sections remain explicitly unavailable, never empty.
    """
    client._check_campaign(campaign_id)
    client._check_id(entity_id, "entity_id")
    selected = list(SECTIONS) if sections is None else sections
    if not isinstance(selected, list) or any(
        section not in SECTIONS for section in selected
    ):
        raise ValueError("Unsupported entity sections")
    if len(set(selected)) != len(selected):
        raise ValueError("Entity sections must not contain duplicates")
    pages = {} if section_pages is None else section_pages
    if not isinstance(pages, dict) or any(section not in selected for section in pages):
        raise ValueError("Continuation pages must refer to selected sections")
    for section, page in pages.items():
        client._check_id(page, f"section_pages[{section}]")
        if section == "images" and page != 1:
            raise ValueError("Images do not support pagination")

    base = f"campaigns/{campaign_id}/entities/{entity_id}"
    async with client.account_reads() as account:
        core = await client.get_entity(campaign_id, entity_id)
        entity = core["entity"]
        module = ENTITY_ENDPOINTS.get(entity["entity_type"])
        result: dict[str, Any] = {
            **core,
            "account_limits": account,
            "sources": {
                "entity": API_BASE_URL + base,
                "details": (
                    f"{API_BASE_URL}campaigns/{campaign_id}"
                    f"/{module}/{entity['child_id']}"
                )
                if module
                else None,
            },
            "sections": {},
            "not_requested": [
                section for section in SECTIONS if section not in selected
            ],
            "continuation": {},
            "missing_data": list(core.get("missing_data", [])),
        }
        exhausted = False
        for section in selected:
            suffix = ENTITY_COLLECTIONS.get(
                section, "image" if section == "images" else section
            )
            source = API_BASE_URL + base + "/" + suffix
            start = pages.get(section, 1)
            part: dict[str, Any] = {
                "data": None if section == "images" else [],
                "source": source,
                "page": None if section == "images" else start,
                "next_page": None,
                "complete": False,
                "missing_data": [],
                "status": "ok",
            }
            page = start
            try:
                if exhausted:
                    raise KankaError(
                        "Read budget exhausted", code="read_budget_exhausted"
                    )
                if section == "images":
                    part.update(
                        data=await client.get_entity_images(campaign_id, entity_id),
                        complete=True,
                    )
                else:
                    readers = {
                        "posts": client.get_entity_posts,
                        "inventory": client.get_entity_inventory,
                        "relations": client.get_entity_relations,
                    }
                    for _ in range(MAX_CAMPAIGN_PAGES):
                        if section in readers:
                            batch = await readers[section](
                                campaign_id, entity_id, page=page
                            )
                        else:
                            batch = await client.get_entity_collection(
                                campaign_id, entity_id, section, page=page
                            )
                        part["data"].extend(batch["data"])
                        part["missing_data"].extend(batch["missing_data"])
                        part["next_page"] = batch["next_page"]
                        if batch["next_page"] is None:
                            part["complete"] = start == 1 and not part["missing_data"]
                            break
                        page = batch["next_page"]
                    else:
                        part["missing_data"].append(
                            {"source": source, "reason": "page_budget"}
                        )
                    if part["next_page"] is not None:
                        result["continuation"][section] = part["next_page"]
            except KankaError as error:
                if error.code == "read_budget_exhausted":
                    exhausted = True
                    part["status"] = "deferred"
                    part["next_page"] = None if section == "images" else page
                    result["continuation"][section] = page
                    part["missing_data"].append(
                        {"source": source, "reason": "read_budget"}
                    )
                elif error.status_code in (403, 404):
                    part.update(data=None, status="unavailable", next_page=None)
                    part["missing_data"] = [
                        {"source": source, "reason": f"http_{error.status_code}"}
                    ]
                else:
                    raise
            if start > 1:
                part["missing_data"].append(
                    {"source": source, "reason": "earlier_pages_not_included"}
                )
            if not part["complete"] and part["status"] == "ok":
                part["status"] = "partial"
            result["sections"][section] = part
            result["missing_data"].extend(part["missing_data"])
        return result
