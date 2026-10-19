"""Compare scoped creation read-back to the approved fields and response IDs."""

from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from loremaster.creation import (
    CharacterDraft,
    CreationDraft,
    OrganisationDraft,
    creation_kind,
    resolve_creation_request,
)
from loremaster.kanka import KankaError


def entry_matches(expected: Any, actual: Any) -> bool:
    """Accept exact text or Kanka's single paragraph around plain text only."""
    return (
        isinstance(expected, str)
        and isinstance(actual, str)
        and (
            expected == actual
            or (
                "<" not in expected
                and ">" not in expected
                and actual == f"<p>{escape(expected, quote=False)}</p>"
            )
        )
    )


def matches_creation_fields(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Compare approved fields using observed API quantity/request-only semantics."""
    for key, value in expected.items():
        if key == "two_way":
            continue  # Request-only field; no live one-way guarantee is inferred.
        observed = actual.get(key)
        if key == "entry":
            if not entry_matches(value, observed):
                return False
        elif key in ("races", "locations", "tags"):
            if (
                not isinstance(observed, list)
                or any(type(uid) is not int or uid <= 0 for uid in observed)
                or sorted(observed) != sorted(value)
            ):
                return False
        elif key == "amount":
            try:
                if type(observed) not in (str, int, float) or Decimal(
                    str(observed)
                ) != Decimal(value):
                    return False
            except InvalidOperation:
                return False
        elif type(observed) is not type(value) or observed != value:
            return False
    return True


def verify_result_ids(journal: dict[str, Any], bundle: dict[str, Any]) -> None:
    """A replaced lookalike attachment must not count as the recorded result."""
    for section, prefix in (
        ("relations", "relation:"),
        ("inventory", "inventory:"),
        ("posts", "post:"),
        ("members", "member:"),
    ):
        if section not in bundle["sections"]:
            continue
        expected = {row["id"] for key, row in journal.items() if key.startswith(prefix)}
        rows = bundle["sections"][section]["data"]
        if any(type(row.get("id")) is not int or row["id"] <= 0 for row in rows):
            raise KankaError(f"Created {section} have invalid record IDs")
        observed = {row["id"] for row in rows}
        if expected != observed:
            raise KankaError(f"Created {section} record IDs changed; reconcile first")


def verify_created(
    draft: CreationDraft, bundle: dict[str, Any], module_ids: dict[int, int]
) -> None:
    """Verify requested fields; accept numeric quantities and actual relation schemas.

    Kanka need not echo the request-only two_way field. This does not establish
    one-way behavior. Collection completeness and exact attachment counts matter.
    """
    if bundle["missing_data"] or any(
        not part["complete"] for part in bundle["sections"].values()
    ):
        raise KankaError("Created entity could not be read back completely")
    details = creation_details(draft, bundle)
    _path, core = resolve_creation_request(
        draft, creation_kind(draft), module_ids=module_ids
    )
    if not matches_creation_fields(core, details):
        raise KankaError("Created entity differs from the approved draft")
    if isinstance(draft, OrganisationDraft):
        expected = {
            "members": [
                resolve_creation_request(
                    draft,
                    f"member:{index}",
                    child_id=details["id"],
                    module_ids=module_ids,
                )[1]
                for index in range(len(draft.members))
            ]
        }
    elif not isinstance(draft, CharacterDraft):
        expected = {}
    else:
        expected = {
            "posts": [post.model_dump() for post in draft.posts],
            "relations": [
                {**relation.model_dump(), "owner_id": details["entity_id"]}
                for relation in draft.relations
            ],
            "inventory": [
                {
                    "item_id": module_ids[item.item_entity_id],
                    "amount": str(item.amount),
                    "is_equipped": item.is_equipped,
                    "position": item.position,
                }
                for item in draft.inventory
            ],
        }
    for section, rows in expected.items():
        actual = bundle["sections"][section]["data"]
        if len(actual) != len(rows):
            raise KankaError(f"Created {section} differ from the approved draft")
        # Collections are multisets: API ordering is not an identity guarantee.
        remaining = list(actual)
        for row in rows:
            for index, candidate in enumerate(remaining):
                if matches_creation_fields(row, candidate):
                    remaining.pop(index)
                    break
            else:
                raise KankaError(f"Created {section} differ from the approved draft")


def creation_details(draft: CreationDraft, bundle: dict[str, Any]) -> dict[str, Any]:
    """Verify requested parent IDs from generic metadata, where Kanka exposes them."""
    details = bundle["details"]
    parent = getattr(draft, "parent_entity_id", None)
    if parent is None:
        return details
    observed = bundle.get("entity", {}).get("parent_id")
    if type(observed) is not int or observed != parent:
        raise KankaError("Created entity parent differs from the approved draft")
    if "parent_id" in details and (
        type(details["parent_id"]) is not int or details["parent_id"] != observed
    ):
        raise KankaError("Module and generic parent identities disagree")
    return {**details, "parent_id": observed}
