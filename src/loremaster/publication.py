"""Exact-content public release review; no source label is an approval grant."""

import secrets
from typing import Any

from loremaster.kanka import KankaClient, KankaError
from loremaster.note_store import digest


def public_projection(
    core: dict[str, Any], post: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Limit releasable canon evidence to public names/descriptions and posts."""
    entity, details = core["entity"], core["details"]
    if details.get("is_private") is not False or (
        "is_private" in entity and entity["is_private"] is not False
    ):
        raise ValueError("Public references require an explicitly public entity")
    if not isinstance(details.get("name"), str) or (
        details.get("entry") is not None and not isinstance(details["entry"], str)
    ):
        raise ValueError("Public source text is malformed")
    result = {
        "entity_id": entity["id"],
        "child_id": entity["child_id"],
        "entity_type": entity["entity_type"],
        "name": details.get("name"),
        "entry": details.get("entry"),
    }
    if post is not None:
        if (
            type(post.get("id")) is not int
            or post["id"] <= 0
            or type(post.get("entity_id")) is not int
            or post["entity_id"] != entity["id"]
            or type(post.get("visibility_id")) is not int
            or post["visibility_id"] != 1
            or post.get("is_private") is True
            or not isinstance(post.get("name"), str)
            or (post.get("entry") is not None and not isinstance(post["entry"], str))
        ):
            raise ValueError(
                "Public citations require a public post on its verified owner"
            )
        result["post"] = {
            key: post.get(key)
            for key in ("id", "entity_id", "name", "entry", "visibility_id")
        }
    return result


async def public_source(
    reader: KankaClient, campaign_id: int, source: dict[str, Any]
) -> dict[str, Any]:
    """GET only fixed public source endpoints; no upstream text is an instruction."""
    reader._check_id(source.get("entity_id"), "entity_id")
    section = source.get("section")
    if section not in ("core", "posts"):
        raise ValueError("Unsupported publication evidence section")
    core = await reader.get_entity(campaign_id, source["entity_id"])
    post = None
    if section == "posts":
        reader._check_id(source.get("record_id"), "record_id")
        post = (
            await reader._get(
                f"campaigns/{campaign_id}/entities/{source['entity_id']}/posts/{source['record_id']}"
            )
        )["data"]
        if not isinstance(post, dict) or post.get("id") != source["record_id"]:
            raise ValueError("Publication post identity changed")
    return public_projection(core, post)


async def verify_publication_sources(
    reader: KankaClient, campaign_id: int, provenance: dict[str, Any] | None
) -> None:
    """Recheck source privacy/content before a public write; never change visibility."""
    review = publication_review(provenance)
    if review is None:
        return
    sources = review.get("public_sources")
    if not isinstance(sources, list) or len(sources) > 20:
        raise KankaError(
            "Invalid publication source budget", code="publication_source_changed"
        )
    for source in sources:
        try:
            if not isinstance(source, dict):
                raise ValueError("Invalid publication source")
            current = await public_source(reader, campaign_id, source)
            if digest(current) != source.get("fingerprint"):
                raise ValueError("Publication source changed")
        except (ValueError, KeyError, TypeError) as error:
            raise KankaError(
                "Publication source visibility or content changed; "
                "refresh the note preview",
                code="publication_source_changed",
            ) from error


def publication_review(provenance: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a stored release manifest without accepting caller approval flags."""
    if not provenance or "publication" not in provenance:
        return None
    review = provenance["publication"]
    if (
        not isinstance(review, dict)
        or review.get("audience") != "all"
        or not isinstance(review.get("review_digest"), str)
        or review["review_digest"]
        != digest({k: v for k, v in review.items() if k != "review_digest"})
    ):
        raise PermissionError("Publication review failed integrity validation")
    return review


def require_publication_review(
    provenance: dict[str, Any] | None,
    reviewed_digest: str | None,
    draft: dict[str, Any],
) -> None:
    """Require trusted terminal acknowledgment of the exact source/public copy."""
    review = publication_review(provenance)
    if review is None:
        if reviewed_digest is not None:
            raise PermissionError("This proposal has no publication review")
        return
    if (
        review.get("concrete_draft") != draft
        or not isinstance(reviewed_digest, str)
        or not reviewed_digest.isascii()
        or not secrets.compare_digest(reviewed_digest, review["review_digest"])
    ):
        raise PermissionError(
            "Public publication requires exact terminal release review"
        )
