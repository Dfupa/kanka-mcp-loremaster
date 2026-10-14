"""Read-only, asynchronous Kanka API access with explicit campaign boundaries."""

from collections.abc import Mapping
from unicodedata import normalize
from typing import Any
from urllib.parse import quote

import httpx

API_BASE_URL = "https://api.kanka.io/1.0/"
MAX_CAMPAIGN_PAGES = 20

# A generic entity_id must be resolved to the module's own child_id.
ENTITY_ENDPOINTS: Mapping[str, str] = {
    "ability": "abilities",
    "calendar": "calendars",
    "character": "characters",
    "creature": "creatures",
    "event": "events",
    "family": "families",
    "item": "items",
    "journal": "journals",
    "location": "locations",
    "map": "maps",
    "note": "notes",
    "organisation": "organisations",
    "quest": "quests",
    "race": "races",
    "tag": "tags",
    "timeline": "timelines",
}


class KankaError(Exception):
    """An upstream response or transport error that is safe to report to users."""


class KankaClient:
    """Read Kanka records without exposing write operations.

    Args:
        token: Personal API token, never written to logs or returned results.
        allowed_campaign_ids: Optional campaign allowlist; None permits every
            campaign authorized by the token.
        transport: Optional HTTPX transport for deterministic offline tests.

    Call ``aclose`` on application shutdown to release pooled connections.
    """

    def __init__(
        self,
        token: str,
        *,
        allowed_campaign_ids: frozenset[int] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("KANKA_API_TOKEN must be a non-empty string")
        if allowed_campaign_ids is not None and any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in allowed_campaign_ids
        ):
            raise ValueError("Campaign allowlist must contain positive integer IDs")
        self._allowed_campaign_ids = allowed_campaign_ids
        self._http = httpx.AsyncClient(
            base_url=API_BASE_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token.strip()}",
            },
            timeout=15.0,
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """Release HTTP connections owned by this client."""
        await self._http.aclose()

    def _check_campaign(self, campaign_id: int) -> None:
        """Reject malformed and disallowed campaign IDs before HTTP requests."""
        if (
            not isinstance(campaign_id, int)
            or isinstance(campaign_id, bool)
            or campaign_id <= 0
        ):
            raise ValueError("campaign_id must be a positive integer")
        if (
            self._allowed_campaign_ids is not None
            and campaign_id not in self._allowed_campaign_ids
        ):
            raise PermissionError("Campaign is not in the configured allowlist")

    @staticmethod
    def _check_id(value: int, name: str) -> None:
        """Validate an external record ID without accepting booleans as ints."""
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    async def _get(
        self, path: str, *, params: dict[str, int | str] | None = None
    ) -> dict[str, Any]:
        """GET and validate one Kanka JSON envelope; never leak bearer tokens."""
        try:
            response = await self._http.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise KankaError(f"Kanka returned HTTP {code}") from None
        except httpx.RequestError:
            raise KankaError("Kanka request failed or timed out") from None
        try:
            payload = response.json()
        except ValueError:
            raise KankaError("Kanka returned invalid JSON") from None
        if not isinstance(payload, dict) or "data" not in payload:
            raise KankaError("Kanka returned an invalid response envelope")
        return payload

    async def list_campaigns(self) -> list[dict[str, Any]]:
        """List accessible campaigns across pages, honoring the read allowlist.

        Raises:
            KankaError: If paging is incomplete or the API returns bad data.
        """
        campaigns = await self._list_rows("campaigns")
        return [
            campaign
            for campaign in campaigns
            if self._allowed_campaign_ids is None
            or campaign["id"] in self._allowed_campaign_ids
        ]

    async def find_entities_by_name(
        self, campaign_id: int, name: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Find entity records by name, resolving IDs directly from Kanka.

        Kanka's ``entities?name=`` filter is a substring search. Return exact
        matches separately; never guess an ID when names are ambiguous.

        Args:
            campaign_id: A campaign accessible to the configured token.
            name: Non-empty entity name, in its original language.

        Returns:
            Exact and partial name matches with their universal entity IDs.

        Raises:
            ValueError: For invalid campaign IDs or an empty name.
            KankaError: On invalid responses or incomplete pagination.
        """
        self._check_campaign(campaign_id)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        requested = normalize("NFC", name.strip()).casefold()
        rows = await self._list_rows(
            f"campaigns/{campaign_id}/entities", params={"name": name.strip()}
        )
        matches: dict[str, list[dict[str, Any]]] = {"exact": [], "partial": []}
        for row in rows:
            if not isinstance(row.get("name"), str):
                raise KankaError("Invalid entity name")
            candidate = {
                "entity_id": row["id"],
                "name": row["name"],
                "entity_type": row.get("entity_type"),
                "child_id": row.get("child_id"),
            }
            exact = normalize("NFC", row["name"]).casefold() == requested
            matches["exact" if exact else "partial"].append(candidate)
        return matches

    async def _list_rows(
        self, path: str, *, params: dict[str, int | str] | None = None
    ) -> list[dict[str, Any]]:
        """Read every page, rejecting malformed or truncated listings."""
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_CAMPAIGN_PAGES + 1):
            response = await self._get(path, params={**(params or {}), "page": page})
            batch = response["data"]
            if not isinstance(batch, list) or not all(
                isinstance(row, dict)
                and type(row.get("id")) is int
                and row["id"] > 0
                for row in batch
            ):
                raise KankaError("Invalid entity listing")
            rows.extend(batch)
            meta = response.get("meta", {})
            links = response.get("links", {})
            if not isinstance(meta, dict) or not isinstance(links, dict):
                raise KankaError("Invalid pagination metadata")
            last = meta.get("last_page")
            if last is not None:
                if type(last) is not int or last < page:
                    raise KankaError("Invalid pagination last_page")
                has_next = page < last
            else:
                has_next = bool(links.get("next"))
            if not has_next:
                return rows
        raise KankaError("Listing exceeded the pagination safety limit")

    async def search_entities(
        self, campaign_id: int, query: str, *, page: int = 1
    ) -> dict[str, Any]:
        """Search names and aliases within one campaign; return one API page.

        Args:
            campaign_id: Authorized campaign ID.
            query: Nonempty search term in the user's original language.
            page: One-based Kanka pagination index.
        """
        self._check_campaign(campaign_id)
        self._check_id(page, "page")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        safe_query = quote(query.strip(), safe="")
        return await self._get(
            f"campaigns/{campaign_id}/search/{safe_query}", params={"page": page}
        )

    async def get_entity(self, campaign_id: int, entity_id: int) -> dict[str, Any]:
        """Resolve a universal entity ID and fetch its module-specific details.

        Unknown custom modules return generic metadata without guessing a URL.
        The related=1 option includes Kanka's available related resources.
        """
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        response = await self._get(f"campaigns/{campaign_id}/entities/{entity_id}")
        entity = response["data"]
        if not isinstance(entity, dict) or type(entity.get("id")) is not int:
            raise KankaError("Invalid entity details")
        if entity["id"] != entity_id:
            raise KankaError("Kanka returned a mismatched entity ID")
        if entity.get("campaign_id") != campaign_id:
            raise KankaError("Kanka returned an entity from a different campaign")
        endpoint = ENTITY_ENDPOINTS.get(entity.get("entity_type"))
        child_id = entity.get("child_id")
        if endpoint is None or type(child_id) is not int or child_id <= 0:
            return {"entity": entity, "details": None}
        details = await self._get(
            f"campaigns/{campaign_id}/{endpoint}/{child_id}",
            params={"related": 1},
        )
        record = details["data"]
        if not isinstance(record, dict) or record.get("id") != child_id:
            raise KankaError("Kanka returned mismatched module details")
        return {"entity": entity, "details": record}

    async def get_entity_posts(
        self, campaign_id: int, entity_id: int, *, page: int = 1
    ) -> dict[str, Any]:
        """Return one page of posts using the universal entity ID."""
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        self._check_id(page, "page")
        response = await self._get(
            f"campaigns/{campaign_id}/entities/{entity_id}/posts",
            params={"page": page},
        )
        if not isinstance(response["data"], list):
            raise KankaError("Invalid posts listing")
        return response
