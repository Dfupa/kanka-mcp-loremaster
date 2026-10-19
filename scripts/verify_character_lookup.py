"""GET-only smoke check using the canonical client and public-safe output.

Credentials come from KANKA_API_TOKEN; no payloads or credentials are saved.
"""

import asyncio
import os
from time import monotonic
from unicodedata import normalize

import httpx

from loremaster.kanka import KankaClient, KankaError

NAMES = ("Marcian", "Roderick", "Elgith")
FALLBACK_CAMPAIGN_ID = 33742
MAX_REQUESTS = 100
MAX_CAMPAIGNS = 5


class ReadOnlyBudget:
    """Pace all requests, including pagination, detail reads and retries."""

    def __init__(self) -> None:
        self.requests = 0
        self.last_request = 0.0

    async def __call__(self, request: httpx.Request) -> None:
        """Reject writes or an exhausted budget before transmitting anything."""
        if request.method != "GET" or self.requests >= MAX_REQUESTS:
            raise KankaError("Read-only request budget exceeded")
        delay = 2.1 - (monotonic() - self.last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self.last_request = monotonic()
        self.requests += 1


async def verify_campaign(client: KankaClient, campaign_id: int) -> bool | None:
    """Verify unique characters; return None when API errors are inconclusive.

    Only fixed diagnostics are printed, never names or upstream payloads.
    """
    verified = True
    inconclusive = False
    for index, name in enumerate(NAMES, 1):
        try:
            matches = await client.find_entities_by_name(campaign_id, name)
            exact = matches["exact"]
            if len(exact) != 1 or exact[0]["entity_type"] != "character":
                print(f"Target {index}: FAIL (missing or ambiguous character)")
                verified = False
                continue
            candidate = exact[0]
            result = await client.get_entity(campaign_id, candidate["entity_id"])
            entity = result["entity"]
            if (
                entity["entity_type"] != "character"
                or entity["child_id"] != candidate["child_id"]
                or normalize("NFC", entity["name"]).casefold() != name.casefold()
                or result["details"] is None
            ):
                print(f"Target {index}: FAIL (identity changed or incomplete)")
                verified = False
                continue
            print(f"Target {index}: PASS (unique character identity verified)")
        except (KankaError, ValueError, PermissionError):
            print(f"Target {index}: FAIL (API, access or response validation)")
            inconclusive = True
    return None if inconclusive else verified


async def verify(client: KankaClient) -> bool:
    """Try visible campaigns, then the explicit troubleshooting fallback.

    Recheck a visible fallback once if its initial lookup was inconclusive.
    Complete negative or ambiguous searches are never repeated.
    """
    try:
        campaigns = await client.list_campaigns()
    except KankaError:
        print("Campaign discovery failed; checking the requested fallback")
        campaigns = []
    campaign_ids = list(dict.fromkeys(row["id"] for row in campaigns))
    if len(campaign_ids) > MAX_CAMPAIGNS:
        print("FAIL: campaign discovery exceeded the smoke-check limit")
        return False
    if FALLBACK_CAMPAIGN_ID not in campaign_ids:
        campaign_ids.append(FALLBACK_CAMPAIGN_ID)
    retry_fallback = False
    for index, campaign_id in enumerate(campaign_ids, 1):
        print(f"Checking campaign candidate {index}")
        status = await verify_campaign(client, campaign_id)
        if status is True:
            print("PASS: all requested character identities verified")
            return True
        if status is None and campaign_id == FALLBACK_CAMPAIGN_ID:
            retry_fallback = True
    if retry_fallback:
        print("Rechecking inconclusive fallback")
        if await verify_campaign(client, FALLBACK_CAMPAIGN_ID) is True:
            print("PASS: all requested character identities verified")
            return True
    print("FAIL: identity verification incomplete")
    return False


async def main() -> None:
    """Use environment credentials and always close the connection pool."""
    token = os.environ.get("KANKA_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("FAIL: KANKA_API_TOKEN is not configured")
    client = KankaClient(token)
    client._http.event_hooks["request"].append(ReadOnlyBudget())
    try:
        success = await verify(client)
    finally:
        await client.aclose()
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
