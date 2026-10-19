"""Serve created mock state over stdio; never installed as a product entry point."""

import asyncio
import sys
from copy import deepcopy
from unittest.mock import patch

import httpx
from creation_fixtures import approved, offline

from loremaster.server import create_server


async def main():
    def no_network(*_args, **_kwargs):
        raise RuntimeError("Real networking is forbidden")

    with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", no_network):
        rehearsal = offline.OfflineCreation()
        baseline = deepcopy(rehearsal.records)
        try:
            draft = offline.example_draft()
            result = await rehearsal.run(approved(rehearsal, draft))
            if result["status"] != "verified":
                raise RuntimeError("Synthetic creation failed")
            server = create_server(rehearsal.reader)
            await server.run_stdio_async()
        finally:
            await rehearsal.aclose()
        for uid, record in baseline.items():
            if rehearsal.records[uid] != record or rehearsal.attachments.get(uid):
                raise RuntimeError("Seeded data changed")
        mutations = [row for row in rehearsal.requests if row[0] != "GET"]
        if [method for method, _path in mutations] != ["POST"] * 5:
            raise RuntimeError("Unexpected mutation trace")
        if not rehearsal._writer.is_closed or not rehearsal.reader._http.is_closed:
            raise RuntimeError("Mock clients were not closed")
        print(
            "CREATION_SEEDS_UNCHANGED FIVE_POSTS_ONLY CLIENTS_CLOSED", file=sys.stderr
        )


if __name__ == "__main__":
    asyncio.run(main())
