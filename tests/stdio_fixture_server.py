"""Launch the real CLI with a synthetic HTTP transport and dummy credentials."""

import sys
from unittest.mock import patch

import httpx
from fake_kanka import handler

from loremaster.kanka import KankaClient
from loremaster.server import main


class TrackingTransport(httpx.MockTransport):
    """Emit a non-sensitive stderr marker when server shutdown closes HTTP."""

    async def aclose(self) -> None:
        await super().aclose()
        print("HTTP_TRANSPORT_CLOSED", file=sys.stderr, flush=True)


def configured_client(token: str, **kwargs: object) -> KankaClient:
    """Keep CLI environment validation and replace only network transport."""
    return KankaClient(token, transport=TrackingTransport(handler), **kwargs)


if __name__ == "__main__":
    with patch("loremaster.server.KankaClient", configured_client):
        main()
