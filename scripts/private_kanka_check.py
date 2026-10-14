"""Run a bounded, GET-only Kanka check and save an encrypted report."""

import asyncio
import base64
import json
import os
import secrets
from pathlib import Path
from time import monotonic
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAxWbYtOxO+KMecKHtNpkR
I1Dm3ZLtdIRHrl6t87oBnM77E/X78GSQAoPHVsjFiFx7C8pN1/S3sgRO6czIRcKe
bDZ5tRA1YgYUVwwWOpCUcnvxRHWVV1RuKe7zWvu/RzTXJrTFtyRcAT5Wr1tCYbfy
UsoSv+ZaOTxPxClkklhGjeiHrpAralbtYDl1XLFHl8N/5FDZDp87gMcTrMiTwSFn
oMQV3RJ2rYACpHyXpg7TbqfY1RekkfoJLAgF+UsZfU2N2i60lXJ6VDTcbWjNDvNN
9WdgfF4OgHiXS3o7bSBuBIotZXquCMA/xNfMyKjpZtiWmIzVC20q9ff5Tx4W+jJj
pxuYutjrjESg7T4Atl4OhrcHUW6rXL1I9K6B7jADtCY8FlsQqOzcKirOsrZPigbA
k1oifXAF95dIRnAcT9ZRkzPxeWARHsuNOfdvGbnZf8XFFmHUOATyRWaXIBRGwWhw
ZYsPlyhHlm1fvLDzarP7iFImosXLRn3C4vjwg70C+N+/AgMBAAE=
-----END PUBLIC KEY-----
"""
BASE = "https://api.kanka.io/1.0/"
NAMES = ("Marcian", "Roderick", "Elgith")
LABEL = b"LoreMaster read-only check 2026-10-14"


async def query() -> dict:
    """Return matches in token-visible campaigns, without modifying Kanka."""
    token = os.environ.get("KANKA_API_TOKEN", "").strip()
    if not token:
        return {"error": "KANKA_API_TOKEN missing"}
    client = httpx.AsyncClient(
        base_url=BASE,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=20.0,
        follow_redirects=False,
    )
    last_call = 0.0

    async def get(path: str, page: int = 1) -> dict:
        """Validate one API envelope and respect the default rate limit."""
        nonlocal last_call
        assert page > 0 and path and not path.startswith("http")
        delay = 2.1 - (monotonic() - last_call)
        if last_call and delay > 0:
            await asyncio.sleep(delay)
        last_call = monotonic()
        try:
            response = await client.get(path, params={"page": page})
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            data = response.json()
            if not isinstance(data, dict) or not isinstance(
                data.get("data"), (dict, list)
            ):
                raise RuntimeError("Malformed response")
            return data
        except httpx.RequestError:
            raise RuntimeError("Connection error") from None

    async def rows(path: str, max_pages: int = 3) -> list[dict]:
        """Read a bounded result collection and flag incomplete pagination."""
        found = []
        for page in range(1, max_pages + 1):
            payload = await get(path, page)
            if not isinstance(payload["data"], list):
                raise RuntimeError("Expected list")
            found.extend(payload["data"])
            meta = payload.get("meta") or {}
            links = payload.get("links") or {}
            last = meta.get("last_page")
            if last is not None:
                more = page < last
            else:
                more = bool(links.get("next"))
            if not more:
                return found
        raise RuntimeError("Pagination limit reached")

    result: dict = {"read_only": True, "results": {}, "errors": []}
    try:
        campaigns = await rows("campaigns", max_pages=5)
        if len(campaigns) > 15:
            raise RuntimeError("More than 15 campaigns")
        for name in NAMES:
            result["results"][name] = []
        for campaign in campaigns:
            campaign_id = campaign.get("id")
            if type(campaign_id) is not int or campaign_id <= 0:
                raise RuntimeError("Invalid campaign ID")
            for name in NAMES:
                path = f"campaigns/{campaign_id}/search/{quote(name, safe='')}"
                try:
                    matches = await rows(path)
                    for entity in matches:
                        if entity.get("name", "").strip().casefold() != name.casefold():
                            continue
                        entity_id = entity.get("entity_id")
                        if type(entity_id) is not int or entity_id <= 0:
                            continue
                        item = {
                            "campaign": campaign.get("name"),
                            "campaign_id": campaign_id,
                            "name": entity["name"],
                            "type": entity.get("type"),
                            "entity_id": entity_id,
                        }
                        try:
                            base = f"campaigns/{campaign_id}/entities/{entity_id}"
                            if name == "Marcian":
                                generic = (await get(base))["data"]
                                item["entity"] = {
                                    key: generic.get(key)
                                    for key in ("name", "entity_type", "child_id")
                                }
                                child_id = generic.get("child_id")
                                if generic.get("entity_type") == "character" and (
                                    type(child_id) is int
                                ):
                                    detail = (await get(
                                        f"campaigns/{campaign_id}/characters/{child_id}"
                                    ))["data"]
                                    item["character"] = {
                                        key: detail.get(key)
                                        for key in (
                                            "name", "title", "age", "type",
                                            "entry", "is_dead"
                                        )
                                    }
                            elif name == "Roderick":
                                items = await rows(f"{base}/inventory")
                                item["inventory"] = [
                                    {
                                        key: entry.get(key)
                                        for key in (
                                            "id", "name", "item_id", "item",
                                            "amount", "position", "is_equipped"
                                        )
                                    }
                                    for entry in items
                                ]
                            else:
                                relations = await rows(f"{base}/relations")
                                item["relations"] = [
                                    {
                                        key: relation.get(key)
                                        for key in (
                                            "id", "owner_id", "target_id", "target",
                                            "relation", "attitude", "is_pinned"
                                        )
                                    }
                                    for relation in relations
                                ]
                        except (RuntimeError, TypeError, ValueError) as error:
                            item["error"] = str(error)
                        result["results"][name].append(item)
                except (RuntimeError, TypeError, ValueError) as error:
                    result["errors"].append({
                        "name": name,
                        "campaign_id": campaign_id,
                        "error": str(error),
                    })
    finally:
        await client.aclose()
    return result


def encrypt(report: dict) -> None:
    """Protect JSON with AES-GCM, wrapping the key with an RSA public key."""
    key = AESGCM.generate_key(bit_length=256)
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(report, ensure_ascii=False).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, LABEL)
    public = serialization.load_pem_public_key(PUBLIC_KEY)
    wrapped = public.encrypt(
        key,
        padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    encoded = {
        "version": 1,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "wrapped_key": base64.b64encode(wrapped).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    destination = Path("secure-result/report.encrypted.json")
    destination.parent.mkdir(exist_ok=True, parents=True)
    destination.write_text(json.dumps(encoded), encoding="utf-8")


if __name__ == "__main__":
    try:
        output = asyncio.run(query())
    except Exception as exc:
        output = {"read_only": True, "error_type": type(exc).__name__}
    encrypt(output)
    print("Encrypted read-only verification report saved.")
