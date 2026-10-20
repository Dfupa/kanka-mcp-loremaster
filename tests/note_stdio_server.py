"""Run real MCP startup with synthetic GET responses and private note storage."""

from unittest.mock import patch

import httpx
from note_fixture import NoteAPI

from loremaster.kanka import KankaClient
from loremaster.server import main


def configured_client(token, **kwargs):
    return KankaClient(token, transport=httpx.MockTransport(NoteAPI()), **kwargs)


if __name__ == "__main__":
    with patch("loremaster.server.KankaClient", configured_client):
        main()
