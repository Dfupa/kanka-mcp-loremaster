"""Compare scoped creation read-back to the approved fields and response IDs."""

from decimal import Decimal, InvalidOperation
from typing import Any

from loremaster.creation import CharacterDraft
from loremaster.kanka import KankaError


def matches_creation_fields(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Compare approved fields using observed API quantity/request-only semantics."""
    for key, value in expected.items():
        if key == "two_way":
            continue  # Request-only field; no live one-way guarantee is inferred.
        observed = actual.get(key)
        if key == "amount":
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
    ):
        expected = {row["id"] for key, row in journal.items() if key.startswith(prefix)}
        rows = bundle["sections"][section]["data"]
        if any(type(row.get("id")) is not int or row["id"] <= 0 for row in rows):
            raise KankaError(f"Created {section} have invalid record IDs")
        observed = {row["id"] for row in rows}
        if expected != observed:
            raise KankaError(f"Created {section} record IDs changed; reconcile first")


def verify_created(
    draft: CharacterDraft, bundle: dict[str, Any], item_ids: dict[int, int]
) -> None:
    """Verify requested fields; accept numeric quantities and actual relation schemas.

    Kanka need not echo the request-only two_way field. This does not establish
    one-way behavior. Collection completeness and exact attachment counts matter.
    """
    if bundle["missing_data"] or any(
        not part["complete"] for part in bundle["sections"].values()
    ):
        raise KankaError("Created entity could not be read back completely")
    details = bundle["details"]
    if any(
        type(details.get(key)) is not type(getattr(draft, key))
        or details.get(key) != getattr(draft, key)
        for key in ("name", "entry", "is_private")
    ):
        raise KankaError("Created entity differs from the approved draft")
    expected = {
        "posts": [post.model_dump() for post in draft.posts],
        "relations": [
            {**relation.model_dump(), "owner_id": details["entity_id"]}
            for relation in draft.relations
        ],
        "inventory": [
            {
                "item_id": item_ids[item.item_entity_id],
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
