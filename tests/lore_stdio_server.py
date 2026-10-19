"""Normal server entry point with all HTTP replaced by a synthetic lore API."""

import json
import sys
from unittest.mock import patch

import httpx
from lore_fixture import LoreAPI

from loremaster.editing import EditService
from loremaster.execution import CreationService
from loremaster.kanka import KankaClient
from loremaster.server import main

api = LoreAPI()
readers = []
writers = []


def reader(token, **kwargs):
    client = KankaClient(token, transport=httpx.MockTransport(api.handle), **kwargs)
    readers.append(client)
    return client


def creator(client, token, store, scope):
    service = CreationService(
        client, token, store, scope, transport=httpx.MockTransport(api.handle)
    )
    writers.append(service)
    return service


def editor(client, token, store, campaigns, operations):
    service = EditService(
        client,
        token,
        store,
        campaigns,
        operations,
        transport=httpx.MockTransport(api.handle),
    )
    writers.append(service)
    return service


async def no_network(*_args, **_kwargs):
    raise AssertionError("Real networking forbidden")


if __name__ == "__main__":
    try:
        with (
            patch("loremaster.server.KankaClient", reader),
            patch("loremaster.server.CreationService", creator),
            patch("loremaster.server.EditService", editor),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", no_network),
        ):
            main()
    finally:
        print(
            "LORE_FIXTURE_RESULT="
            + json.dumps(
                {
                    "mutations": [
                        row for row in api.requests if row[0] in ("POST", "PATCH")
                    ],
                    "records": api.records,
                    "clients_closed": all(client._http.is_closed for client in readers)
                    and all(service._writer.is_closed for service in writers),
                }
            ),
            file=sys.stderr,
        )
