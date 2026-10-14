"""Read-only MCP tools for Kanka LoreMaster."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from loremaster.kanka import KankaClient


def create_server(client: KankaClient) -> FastMCP:
    """Bind the four read-only tools to an injectable Kanka API client.

    Args:
        client: Configured Kanka API client. Ownership is transferred to the
            server, which closes it during the MCP lifespan shutdown.
    """

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    server = FastMCP("Kanka LoreMaster", lifespan=lifespan)

    @server.tool()
    async def list_campaigns() -> list[dict[str, Any]]:
        """List Kanka campaigns visible to the configured token and allowlist."""
        return await client.list_campaigns()

    @server.tool()
    async def search_entities(
        campaign_id: int, query: str, page: int = 1
    ) -> dict[str, Any]:
        """Search a campaign by name or term; returns one page of candidates.

        Search results have both an ``id`` (type-specific) and ``entity_id``
        (universal). Pass ``entity_id`` to get_entity and get_entity_posts.
        """
        return await client.search_entities(campaign_id, query, page=page)

    @server.tool()
    async def find_entities_by_name(
        campaign_id: int, name: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Find named entities and their IDs without relying on text mentions.

        Exact and partial matches are returned separately. When multiple exact
        matches exist, ask the user to disambiguate before using an ID.
        """
        return await client.find_entities_by_name(campaign_id, name)

    @server.tool()
    async def get_entity(campaign_id: int, entity_id: int) -> dict[str, Any]:
        """Read an entity, its module-specific record, and available related data.

        ``entity_id`` is the universal identifier, not a character/location ID.
        This operation never writes to Kanka.
        """
        return await client.get_entity(campaign_id, entity_id)

    @server.tool()
    async def get_entity_posts(
        campaign_id: int, entity_id: int, page: int = 1
    ) -> dict[str, Any]:
        """Read one page of posts attached to a universal entity ID."""
        return await client.get_entity_posts(campaign_id, entity_id, page=page)

    return server


def main() -> None:
    """Start a local stdio MCP server using environment-based credentials."""
    token = os.environ.get("KANKA_API_TOKEN", "")
    if not token.strip():
        raise RuntimeError("Set KANKA_API_TOKEN before starting LoreMaster")
    allowed_raw = os.environ.get("KANKA_ALLOWED_CAMPAIGN_IDS", "").strip()
    try:
        allowed = (
            frozenset(int(part.strip()) for part in allowed_raw.split(","))
            if allowed_raw
            else None
        )
    except ValueError:
        raise ValueError(
            "KANKA_ALLOWED_CAMPAIGN_IDS must contain integer IDs"
        ) from None
    client = KankaClient(token, allowed_campaign_ids=allowed)
    create_server(client).run(transport="stdio")


if __name__ == "__main__":
    main()
