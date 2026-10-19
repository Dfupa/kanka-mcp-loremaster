"""Read-only, asynchronous Kanka API access with explicit campaign boundaries."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from unicodedata import normalize
from urllib.parse import quote

import httpx

API_BASE_URL = "https://api.kanka.io/1.0/"
MAX_CAMPAIGN_PAGES = 20
MAX_RATE_LIMIT_RETRIES = 2
MAX_RETRY_DELAY = 30
MAX_LINKED_RECORDS = 20
MAX_PAGE_ROWS = 100


@dataclass
class ReadBudget:
    """A per-operation safety budget, paced using the actual account quota."""

    rate_limit: int
    deadline: float
    remaining: int = 100
    next_request: float = 0

    async def wait(self) -> None:
        """Reserve a GET attempt, including retries, or stop before sending it."""
        loop = asyncio.get_running_loop()
        delay = max(0.0, self.next_request - loop.time())
        if self.remaining <= 0 or loop.time() + delay >= self.deadline:
            raise KankaError(
                "Read budget exhausted; continue from the returned section pages",
                code="read_budget_exhausted",
            )
        await asyncio.sleep(delay)
        self.remaining -= 1
        self.next_request = loop.time() + 60 / self.rate_limit


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

ENTITY_COLLECTIONS: Mapping[str, str] = {
    "attributes": "attributes",
    "reminders": "reminders",
    "assets": "entity_assets",
    "abilities": "entity_abilities",
    "tags": "entity_tags",
    "mentions": "mentions",
    "permissions": "entity_permissions",
}


class KankaError(Exception):
    """An upstream response or transport error that is safe to report to users."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_response",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _reject_nonfinite_json(_value: str) -> None:
    """Reject non-JSON numeric constants instead of silently accepting NaN/Infinity."""
    raise ValueError("Non-finite JSON number")


def _retry_delay(header: str, attempt: int) -> float:
    """Honor delay-seconds or HTTP-date without unbounded integer conversion."""
    header = header.strip()
    if header.isascii() and header.isdecimal():
        return float(int(header)) if len(header) <= 8 else MAX_RETRY_DELAY + 1
    try:
        deadline = parsedate_to_datetime(header)
        if deadline.tzinfo is not None:
            return max(0.0, (deadline - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        pass
    return float(2 ** (attempt + 1))


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
        self._budget: ContextVar[ReadBudget | None] = ContextVar(
            "kanka_read_budget", default=None
        )
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
        self,
        path: str,
        *,
        params: dict[str, int | str] | None = None,
        require_data: bool = True,
    ) -> dict[str, Any]:
        """GET and validate one Kanka JSON envelope; never leak bearer tokens."""
        try:
            for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
                if budget := self._budget.get():
                    await budget.wait()
                response = await self._http.get(path, params=params)
                if response.status_code != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
                    break
                delay = _retry_delay(response.headers.get("Retry-After", ""), attempt)
                if delay > MAX_RETRY_DELAY:
                    break
                await asyncio.sleep(delay)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise KankaError(
                f"Kanka returned HTTP {code}", code="upstream_http", status_code=code
            ) from None
        except httpx.RequestError:
            raise KankaError(
                "Kanka request failed or timed out", code="transport_error"
            ) from None
        try:
            payload = response.json(parse_constant=_reject_nonfinite_json)
        except ValueError:
            raise KankaError("Kanka returned invalid JSON") from None
        if not isinstance(payload, dict) or (require_data and "data" not in payload):
            raise KankaError("Kanka returned an invalid response envelope")
        return payload

    @asynccontextmanager
    async def account_reads(self) -> AsyncIterator[dict[str, int | None]]:
        """Discover quota without returning personal profile data or guessing tiers.

        Collection page sizes still come from each response's meta.per_page.
        The request/deadline budgets are local safeguards, not subscription limits.
        """
        profile = (await self._get("profile"))["data"]
        if (
            not isinstance(profile, dict)
            or type(profile.get("rate_limit")) is not int
            or profile["rate_limit"] <= 0
        ):
            raise KankaError("Missing or invalid account quota metadata")
        pagination = profile.get("default_pagination")
        if pagination is not None and (type(pagination) is not int or pagination < 0):
            raise KankaError("Missing or invalid account quota metadata")
        # Live profiles can return 0 for an unset preference; meta.per_page wins.
        limits = {"rate_limit": profile["rate_limit"], "default_pagination": pagination}
        token = self._budget.set(
            ReadBudget(profile["rate_limit"], asyncio.get_running_loop().time() + 90)
        )
        try:
            yield limits
        finally:
            self._budget.reset(token)

    @staticmethod
    def _entity_type(entity: dict[str, Any]) -> str:
        """Read the module code from current or legacy generic entity schemas.

        Current generic records use ``type`` for the module and ``type_field``
        for the user-defined subtype. Child records use a different contract.
        """
        code = entity.get(
            "entity_type", entity.get("entity_type_code", entity.get("type"))
        )
        if not isinstance(code, str) or not code.strip():
            raise KankaError("Invalid entity type")
        return code

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
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 1000:
            raise ValueError(
                "name must be a non-empty string of at most 1000 characters"
            )
        requested = normalize("NFC", name.strip()).casefold()
        rows = await self._list_rows(
            f"campaigns/{campaign_id}/entities", params={"name": name.strip()}
        )
        matches: dict[str, list[dict[str, Any]]] = {"exact": [], "partial": []}
        for row in rows:
            if not isinstance(row.get("name"), str):
                raise KankaError("Invalid entity name")
            if "campaign_id" in row and (
                type(row["campaign_id"]) is not int or row["campaign_id"] != campaign_id
            ):
                raise KankaError("Kanka returned an entity from a different campaign")
            candidate_name = normalize("NFC", row["name"]).casefold()
            if requested not in candidate_name:
                continue
            candidate = {
                "campaign_id": campaign_id,
                "entity_id": row["id"],
                "name": row["name"],
                "entity_type": self._entity_type(row),
                "child_id": row.get("child_id"),
            }
            exact = candidate_name == requested
            matches["exact" if exact else "partial"].append(candidate)
        return matches

    async def _list_rows(
        self, path: str, *, params: dict[str, int | str] | None = None
    ) -> list[dict[str, Any]]:
        """Read every page, rejecting malformed or truncated listings."""
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_CAMPAIGN_PAGES + 1):
            response = await self._get(path, params={**(params or {}), "page": page})
            batch, has_next = self._validate_page(response, page)
            rows.extend(batch)
            if not has_next:
                return rows
        raise KankaError("Listing exceeded the pagination safety limit")

    @staticmethod
    def _validate_rows(
        batch: Any, *, require_id: bool = True, max_rows: int = MAX_PAGE_ROWS
    ) -> list[dict[str, Any]]:
        """Reject malformed or oversized collections before processing records."""
        if not isinstance(batch, list) or not all(
            isinstance(row, dict)
            and (
                (not require_id and "id" not in row)
                or (type(row.get("id")) is int and row["id"] > 0)
            )
            for row in batch
        ):
            raise KankaError("Invalid entity listing")
        if len(batch) > max_rows:
            raise KankaError("Listing exceeded the page size safety limit")
        return batch

    @staticmethod
    def _validate_page(
        response: dict[str, Any], page: int, *, require_id: bool = True
    ) -> tuple[list[dict[str, Any]], bool]:
        """Validate rows and paging without following API-supplied URLs."""
        meta, links = response.get("meta", {}), response.get("links", {})
        if not isinstance(meta, dict) or not isinstance(links, dict):
            raise KankaError("Invalid pagination metadata")
        per_page = meta.get("per_page", MAX_PAGE_ROWS)
        if type(per_page) is not int or per_page <= 0:
            raise KankaError("Invalid pagination per_page")
        batch = KankaClient._validate_rows(
            response["data"], require_id=require_id, max_rows=per_page
        )
        if "last_page" not in meta and "next" not in links:
            raise KankaError("Missing pagination completeness metadata")
        current = meta.get("current_page", page)
        if type(current) is not int or current != page:
            raise KankaError("Invalid pagination current_page")
        next_link = links.get("next")
        if next_link is not None and (
            not isinstance(next_link, str) or not next_link.strip()
        ):
            raise KankaError("Invalid pagination next link")
        last = meta.get("last_page")
        if "last_page" in meta:
            if type(last) is not int or last < page:
                raise KankaError("Invalid pagination last_page")
            has_next = page < last
            if "next" in links and bool(next_link) != has_next:
                raise KankaError("Inconsistent pagination metadata")
        else:
            has_next = bool(next_link)
        return batch, has_next

    async def _get_page(
        self, path: str, page: int, *, require_id: bool = True
    ) -> dict[str, Any]:
        """Read one bounded page and explicitly mark whole-collection completeness."""
        payload = await self._get(path, params={"page": page})
        rows, has_next = self._validate_page(payload, page, require_id=require_id)
        return {
            "data": rows,
            "next_page": page + 1 if has_next else None,
            "complete": page == 1 and not has_next,
            "missing_data": [],
        }

    async def search_entities(
        self, campaign_id: int, query: str, *, page: int = 1
    ) -> dict[str, Any]:
        """Read search candidates and verify their universal IDs in one campaign.

        Current search responses have no paging metadata and cannot prove absence.
        Their child_id field is not trusted: verified generic metadata supplies
        module IDs. Legacy responses may instead provide a separate entity_id.

        Args:
            campaign_id: Authorized campaign ID.
            query: Nonempty search term in the user's original language.
            page: One-based Kanka pagination index.
        """
        self._check_campaign(campaign_id)
        self._check_id(page, "page")
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 1000:
            raise ValueError(
                "query must be a non-empty string of at most 1000 characters"
            )
        safe_query = quote(query.strip(), safe="")
        path = f"campaigns/{campaign_id}/search/{safe_query}"
        payload = await self._get(path, params={"page": page})
        if "meta" not in payload and "links" not in payload:
            if page != 1:
                raise KankaError(
                    "Search pagination is unavailable; use find_entities_by_name"
                )
            response = {
                "data": self._validate_rows(payload["data"]),
                "next_page": None,
                "complete": False,
                "missing_data": [
                    {
                        "source": API_BASE_URL + path,
                        "reason": "pagination_unavailable",
                    }
                ],
            }
        else:
            rows, has_next = self._validate_page(payload, page)
            response = {
                "data": rows,
                "next_page": page + 1 if has_next else None,
                "complete": page == 1 and not has_next,
                "missing_data": [],
            }
        if len(response["data"]) > MAX_LINKED_RECORDS:
            raise KankaError(
                "Search has too many candidates; narrow the query "
                "or use find_entities_by_name"
            )
        identities: dict[int, dict[str, Any]] = {}
        for row in response["data"]:
            if "campaign_id" in row and (
                type(row["campaign_id"]) is not int or row["campaign_id"] != campaign_id
            ):
                raise KankaError(
                    "Kanka returned search results from a different campaign"
                )
            universal_id = row.get("entity_id", row["id"])
            if type(universal_id) is not int or universal_id <= 0:
                raise KankaError("Invalid search entity ID")
            if universal_id not in identities:
                identities[universal_id] = await self.get_entity_metadata(
                    campaign_id, universal_id
                )
            identity = identities[universal_id]
            if (
                not isinstance(row.get("name"), str)
                or normalize("NFC", row["name"]).casefold()
                != normalize("NFC", identity["name"]).casefold()
            ):
                raise KankaError("Kanka returned a mismatched search identity")
            row.update(
                entity_id=universal_id,
                child_id=identity.get("child_id"),
                entity_type=identity["entity_type"],
            )
        return response

    async def get_entity_metadata(
        self, campaign_id: int, entity_id: int
    ) -> dict[str, Any]:
        """Verify generic identity within a campaign, omitting embedded lore."""
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        response = await self._get(f"campaigns/{campaign_id}/entities/{entity_id}")
        entity = response["data"]
        if not isinstance(entity, dict) or type(entity.get("id")) is not int:
            raise KankaError("Invalid entity details")
        if entity["id"] != entity_id:
            raise KankaError("Kanka returned a mismatched entity ID")
        if (
            type(entity.get("campaign_id")) is not int
            or entity["campaign_id"] != campaign_id
        ):
            raise KankaError("Kanka returned an entity from a different campaign")
        if not isinstance(entity.get("name"), str) or not entity["name"].strip():
            raise KankaError("Invalid entity name")
        return {
            **{key: value for key, value in entity.items() if key != "child"},
            "entity_type": self._entity_type(entity),
        }

    async def get_entity(self, campaign_id: int, entity_id: int) -> dict[str, Any]:
        """Read verified module details; related collections use their own tools.

        Unknown custom modules return metadata and an explicit missing-data marker.
        """
        entity = await self.get_entity_metadata(campaign_id, entity_id)
        endpoint = ENTITY_ENDPOINTS.get(entity["entity_type"])
        child_id = entity.get("child_id")
        if endpoint is None:
            return {
                "entity": entity,
                "details": None,
                "missing_data": [
                    {
                        "source": (
                            f"{API_BASE_URL}campaigns/{campaign_id}"
                            f"/entities/{entity_id}"
                        ),
                        "reason": "unsupported_module",
                    }
                ],
            }
        if type(child_id) is not int or child_id <= 0:
            raise KankaError("Invalid module child ID")
        details = await self._get(f"campaigns/{campaign_id}/{endpoint}/{child_id}")
        record = details["data"]
        if (
            not isinstance(record, dict)
            or type(record.get("id")) is not int
            or record["id"] != child_id
            or type(record.get("entity_id")) is not int
            or record["entity_id"] != entity_id
            or (
                "campaign_id" in record
                and (
                    type(record["campaign_id"]) is not int
                    or record["campaign_id"] != campaign_id
                )
            )
        ):
            raise KankaError("Kanka returned mismatched module details")
        if (
            not isinstance(entity.get("name"), str)
            or not isinstance(record.get("name"), str)
            or normalize("NFC", entity["name"]).casefold()
            != normalize("NFC", record["name"]).casefold()
        ):
            raise KankaError("Kanka returned a mismatched module name")
        return {"entity": entity, "details": record}

    async def get_organisation_members(
        self, campaign_id: int, entity_id: int
    ) -> dict[str, Any]:
        """Read all memberships through a verified organisation's module ID.

        A missing pagination contract, invalid owner, duplicate record ID or
        incomplete listing fails closed. No membership origin is inferred.
        """
        entity = await self.get_entity_metadata(campaign_id, entity_id)
        organisation_id = entity.get("child_id")
        if entity["entity_type"] != "organisation":
            raise ValueError("Membership owner must identify an organisation")
        self._check_id(organisation_id, "organisation_id")
        path = (
            f"campaigns/{campaign_id}/organisations/{organisation_id}"
            "/organisation_members"
        )
        rows = await self._list_rows(path)
        if len({row["id"] for row in rows}) != len(rows):
            raise KankaError("Organisation membership record IDs are duplicated")
        for row in rows:
            if (
                type(row.get("organisation_id")) is not int
                or row["organisation_id"] != organisation_id
                or type(row.get("character_id")) is not int
                or row["character_id"] <= 0
                or (
                    "campaign_id" in row
                    and (
                        type(row["campaign_id"]) is not int
                        or row["campaign_id"] != campaign_id
                    )
                )
            ):
                raise KankaError(
                    "Kanka returned a membership for a different owner/campaign"
                )
        return {"data": rows, "complete": True, "source": API_BASE_URL + path}

    async def get_entity_posts(
        self, campaign_id: int, entity_id: int, *, page: int = 1
    ) -> dict[str, Any]:
        """Return one page of posts using the universal entity ID."""
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        self._check_id(page, "page")
        response = await self._get_page(
            f"campaigns/{campaign_id}/entities/{entity_id}/posts", page
        )
        for row in response["data"]:
            if type(row.get("entity_id")) is not int or row["entity_id"] != entity_id:
                raise KankaError("Kanka returned posts from a different entity")
        return response

    async def get_entity_collection(
        self, campaign_id: int, entity_id: int, section: str, *, page: int = 1
    ) -> dict[str, Any]:
        """Read a documented attachment collection without expanding linked lore.

        The aggregate verifies the subject first. Ownership fields are checked
        when documented; tags/permissions rely on the scoped endpoint. Missing
        pagination metadata is explicit and never establishes an empty collection.
        """
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        self._check_id(page, "page")
        if section not in ENTITY_COLLECTIONS:
            raise ValueError("Unsupported entity collection")
        path = (
            f"campaigns/{campaign_id}/entities/{entity_id}/"
            f"{ENTITY_COLLECTIONS[section]}"
        )
        payload = await self._get(path, params={"page": page})
        if "meta" not in payload and "links" not in payload:
            if page != 1:
                raise KankaError("Collection pagination is unavailable")
            rows = self._validate_rows(
                payload["data"], require_id=section != "mentions"
            )
            next_page = None
            missing = [
                {"source": API_BASE_URL + path, "reason": "pagination_unavailable"}
            ]
        else:
            rows, has_next = self._validate_page(
                payload, page, require_id=section != "mentions"
            )
            next_page = page + 1 if has_next else None
            missing = []
        for row in rows:
            if (
                section in ("attributes", "assets", "abilities", "mentions")
                or "entity_id" in row
            ):
                if (
                    type(row.get("entity_id")) is not int
                    or row["entity_id"] != entity_id
                ):
                    raise KankaError(
                        "Kanka returned attachments from a different entity"
                    )
            if row.get("campaign_id") is not None and (
                type(row["campaign_id"]) is not int or row["campaign_id"] != campaign_id
            ):
                raise KankaError("Kanka returned attachments from a different campaign")
            if section == "reminders" and (
                type(row.get("remindable_id")) is not int
                or row["remindable_id"] != entity_id
                or not isinstance(row.get("remindable_type"), str)
                or row["remindable_type"].replace("\\", "/") != "App/Models/Entity"
            ):
                raise KankaError("Kanka returned a reminder for a different subject")
        return {
            "data": rows,
            "next_page": next_page,
            "complete": page == 1 and next_page is None and not missing,
            "missing_data": missing,
        }

    async def get_entity_images(
        self, campaign_id: int, entity_id: int
    ) -> dict[str, Any]:
        """Read portrait/header references without downloading or uploading files."""
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        payload = await self._get(
            f"campaigns/{campaign_id}/entities/{entity_id}/image", require_data=False
        )
        images = payload.get("data", payload)
        if not isinstance(images, dict) or not {"image", "header"} <= images.keys():
            raise KankaError("Invalid entity image metadata")
        if any(
            images[key] is not None and not isinstance(images[key], dict)
            for key in ("image", "header")
        ):
            raise KankaError("Invalid entity image references")
        for reference in images.values():
            if isinstance(reference, dict) and any(
                reference.get(key) is not None and not isinstance(reference[key], str)
                for key in ("uuid", "full", "thumbnail")
            ):
                raise KankaError("Invalid entity image reference fields")
        return {key: images[key] for key in ("image", "header")}

    async def get_entity_inventory(
        self, campaign_id: int, entity_id: int, *, page: int = 1
    ) -> dict[str, Any]:
        """Read one inventory page, checking ownership and resolving linked items.

        Item IDs belong to the items module. Each linked item is verified against
        its universal record in the same campaign. Private/deleted links and the
        per-page enrichment limit remain explicit in ``missing_data``.
        """
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        self._check_id(page, "page")
        await self.get_entity_metadata(campaign_id, entity_id)
        response = await self._get_page(
            f"campaigns/{campaign_id}/entities/{entity_id}/inventory", page
        )
        items: dict[int, dict[str, Any] | None] = {}
        for row in response["data"]:
            if type(row.get("entity_id")) is not int or row["entity_id"] != entity_id:
                raise KankaError("Kanka returned inventory from a different entity")
            if type(row.get("is_equipped")) is not bool:
                raise KankaError("Invalid inventory equipped status")
            if type(row.get("amount")) not in (int, float, str):
                raise KankaError("Invalid inventory amount")
            if row.get("name") is not None and not isinstance(row["name"], str):
                raise KankaError("Invalid inventory name")
            item_id = row.get("item_id")
            row["item"] = None
            if item_id is None:
                if not row.get("name"):
                    response["missing_data"].append(
                        {
                            "source": (
                                f"{API_BASE_URL}campaigns/{campaign_id}"
                                f"/entities/{entity_id}/inventory"
                            ),
                            "reason": "missing_item_name",
                        }
                    )
                continue
            if type(item_id) is not int or item_id <= 0:
                raise KankaError("Invalid inventory item ID")
            path = f"campaigns/{campaign_id}/items/{item_id}"
            if item_id not in items:
                if len(items) >= MAX_LINKED_RECORDS:
                    response["missing_data"].append(
                        {
                            "source": API_BASE_URL + path,
                            "reason": "lookup_limit",
                        }
                    )
                    continue
                try:
                    item = (await self._get(path))["data"]
                    if (
                        not isinstance(item, dict)
                        or type(item.get("id")) is not int
                        or item["id"] != item_id
                        or type(item.get("entity_id")) is not int
                        or item["entity_id"] <= 0
                        or (
                            "campaign_id" in item
                            and (
                                type(item["campaign_id"]) is not int
                                or item["campaign_id"] != campaign_id
                            )
                        )
                    ):
                        raise KankaError(
                            "Kanka returned mismatched inventory item details"
                        )
                    identity = await self.get_entity_metadata(
                        campaign_id, item["entity_id"]
                    )
                    if (
                        identity["entity_type"] != "item"
                        or type(identity.get("child_id")) is not int
                        or identity["child_id"] != item_id
                        or not isinstance(item.get("name"), str)
                        or normalize("NFC", identity.get("name", "")).casefold()
                        != normalize("NFC", item["name"]).casefold()
                    ):
                        raise KankaError(
                            "Kanka returned mismatched inventory item identity"
                        )
                    items[item_id] = {
                        "id": item_id,
                        "entity_id": item["entity_id"],
                        "name": item["name"],
                        "type": item.get("type"),
                        "is_private": item.get("is_private"),
                        "source": API_BASE_URL + path,
                    }
                except KankaError as error:
                    if error.status_code not in (403, 404):
                        raise
                    items[item_id] = None
                    response["missing_data"].append(
                        {
                            "source": API_BASE_URL + path,
                            "reason": "unavailable",
                        }
                    )
            row["item"] = items[item_id]
            if not row.get("name") and row["item"] is not None:
                row["name"] = row["item"]["name"]
        response["complete"] = response["complete"] and not response["missing_data"]
        return response

    async def get_entity_relations(
        self, campaign_id: int, entity_id: int, *, page: int = 1
    ) -> dict[str, Any]:
        """Read explicit relation edges, preserving direction and duplicate targets.

        Related names are read from generic entity records in the same campaign.
        A returned edge is not proof of a reciprocal edge or an inferred family tie.
        """
        self._check_campaign(campaign_id)
        self._check_id(entity_id, "entity_id")
        self._check_id(page, "page")
        subject = await self.get_entity_metadata(campaign_id, entity_id)
        response = await self._get_page(
            f"campaigns/{campaign_id}/entities/{entity_id}/relations",
            page,
            require_id=False,
        )
        entities: dict[int, dict[str, Any] | None] = {entity_id: subject}
        for row in response["data"]:
            owner, target = row.get("owner_id"), row.get("target_id")
            if any(type(value) is not int or value <= 0 for value in (owner, target)):
                raise KankaError("Invalid relation endpoint ID")
            if entity_id not in (owner, target):
                raise KankaError("Kanka returned a relation for a different entity")
            if not isinstance(row.get("relation"), str):
                raise KankaError("Invalid relation label")
            attitude = row.get("attitude")
            if attitude is not None and (
                type(attitude) is not int or not -100 <= attitude <= 100
            ):
                raise KankaError("Invalid relation attitude")
            if "is_pinned" in row and type(row["is_pinned"]) is not bool:
                raise KankaError("Invalid relation pinned status")
            for role, related_id in (("owner", owner), ("target", target)):
                path = f"campaigns/{campaign_id}/entities/{related_id}"
                if related_id not in entities:
                    if len(entities) - 1 >= MAX_LINKED_RECORDS:
                        response["missing_data"].append(
                            {
                                "source": API_BASE_URL + path,
                                "reason": "lookup_limit",
                            }
                        )
                        row[f"{role}_name"] = None
                        continue
                    try:
                        entities[related_id] = await self.get_entity_metadata(
                            campaign_id, related_id
                        )
                    except KankaError as error:
                        if error.status_code not in (403, 404):
                            raise
                        entities[related_id] = None
                        response["missing_data"].append(
                            {
                                "source": API_BASE_URL + path,
                                "reason": "unavailable",
                            }
                        )
                related = entities[related_id]
                row[f"{role}_name"] = related.get("name") if related else None
            row["direction"] = (
                "self"
                if owner == target
                else "outgoing"
                if owner == entity_id
                else "incoming"
            )
        response["complete"] = response["complete"] and not response["missing_data"]
        return response
