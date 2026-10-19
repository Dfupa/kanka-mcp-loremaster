"""Launch the normal opt-in CLI with only its HTTP transports mocked."""

import asyncio
import sys
from unittest.mock import patch

import httpx
from creation_fixtures import offline
from supervised_fixture import handle

from loremaster.execution import CreationService
from loremaster.kanka import KankaClient
from loremaster.server import main

if __name__ == "__main__":
    rehearsal = offline.OfflineCreation()

    def client(token, **kwargs):
        return KankaClient(
            token,
            transport=httpx.MockTransport(lambda request: handle(rehearsal, request)),
            **kwargs,
        )

    def service(reader, token, store, scope):
        return CreationService(
            reader,
            token,
            store,
            scope,
            transport=httpx.MockTransport(lambda request: handle(rehearsal, request)),
        )

    async def no_network(*_args, **_kwargs):
        raise RuntimeError("Real networking is forbidden")

    try:
        with (
            patch("loremaster.server.KankaClient", client),
            patch("loremaster.server.CreationService", service),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", no_network),
        ):
            main()
    finally:
        asyncio.run(rehearsal.aclose())
        count = sum(method == "POST" for method, _path in rehearsal.requests)
        print(f"SUPERVISED_POST_COUNT={count} CLIENTS_CLOSED", file=sys.stderr)
