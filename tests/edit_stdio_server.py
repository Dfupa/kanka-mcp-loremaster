"""Normal entry point, with mandatory synthetic transports and no real network."""

import json
import sys
from unittest.mock import patch

import httpx
from edit_fixture import EditAPI

from loremaster.editing import EditService
from loremaster.kanka import KankaClient
from loremaster.server import main

api = EditAPI()
clients = []
services = []


def reader(token, **kwargs):
    client = KankaClient(token, transport=httpx.MockTransport(api.handle), **kwargs)
    clients.append(client)
    return client


def editing(client, token, store, campaigns, operations):
    service = EditService(
        client,
        token,
        store,
        campaigns,
        operations,
        transport=httpx.MockTransport(api.handle),
    )
    services.append(service)
    return service


async def no_network(*_args, **_kwargs):
    raise AssertionError("Real networking is forbidden")


if __name__ == "__main__":
    try:
        with (
            patch("loremaster.server.KankaClient", reader),
            patch("loremaster.server.EditService", editing),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", no_network),
        ):
            main()
    finally:
        print(
            "EDIT_FIXTURE_RESULT="
            + json.dumps(
                {
                    "patches": [row for row in api.requests if row[0] == "PATCH"],
                    "record": api.records[101],
                    "attachments": api.attachments,
                    "clients_closed": all(client._http.is_closed for client in clients)
                    and all(service._writer.is_closed for service in services),
                }
            ),
            file=sys.stderr,
        )
