"""Review/approve/revoke creation or edit proposals; never expose this via MCP."""

import argparse
import asyncio
import getpass
import json
import os
import sqlite3
import sys
from pathlib import Path

from loremaster.approvals import LocalApprovals
from loremaster.edit_models import EDIT_OPERATIONS
from loremaster.edit_store import EditStore
from loremaster.editing import EditService
from loremaster.execution import CreationService
from loremaster.kanka import KankaClient, KankaError


async def reconcile_edit(store: EditStore, database: Path, proposal_id: str) -> None:
    """Review GET-only evidence for the stored edit's exact uncertain target."""
    from loremaster.server import creation_settings

    settings = creation_settings()
    token = os.environ.get("KANKA_API_TOKEN", "").strip()
    if settings is None or settings[0] != database or not token:
        raise ValueError("Reconciliation needs matching write configuration and token")
    _path, scope = settings
    if not scope.operations & EDIT_OPERATIONS:
        raise ValueError("Reconciliation requires explicit edit operation scope")
    allowed_raw = os.environ.get("KANKA_ALLOWED_CAMPAIGN_IDS", "").strip()
    allowed = (
        frozenset(int(part.strip()) for part in allowed_raw.split(","))
        if allowed_raw
        else scope.campaign_ids
    )
    reader = KankaClient(token, allowed_campaign_ids=allowed)
    service = EditService(
        reader, token, store, scope.campaign_ids, scope.operations & EDIT_OPERATIONS
    )
    try:
        campaign_id = store.review(proposal_id)["plan"]["draft"]["campaign_id"]
        evidence = await service.inspect_reconciliation(campaign_id, proposal_id)
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        print("Stop all editing workers and verify the record's origin.")
        phrase = f"reconcile {proposal_id} {evidence['fingerprint']}"
        print(f"To record this matching evidence, type: {phrase}")
        if input("> ").strip() != phrase:
            raise SystemExit("Reconciliation cancelled")
        store.confirm_reconciled(
            proposal_id, evidence["fingerprint"], getpass.getuser()
        )
        print("Reconciliation recorded; fresh approval permits read-back only")
    finally:
        await service._writer.aclose()
        await reader.aclose()


async def reconcile(
    store: LocalApprovals, database: Path, proposal_id: str, candidate_id: int
) -> None:
    """Inspect a selected record and require explicit stopped-worker/origin review."""
    from loremaster.server import creation_settings

    settings = creation_settings()
    token = os.environ.get("KANKA_API_TOKEN", "").strip()
    if settings is None or settings[0] != database or not token:
        raise ValueError(
            "Reconciliation needs matching creation configuration and token"
        )
    _path, scope = settings
    allowed_raw = os.environ.get("KANKA_ALLOWED_CAMPAIGN_IDS", "").strip()
    allowed = (
        frozenset(int(value.strip()) for value in allowed_raw.split(","))
        if allowed_raw
        else scope.campaign_ids
    )
    reader = KankaClient(token, allowed_campaign_ids=allowed)
    service = CreationService(reader, token, store, scope)
    try:
        campaign_id = store.draft(proposal_id).campaign_id
        evidence = await service.inspect_reconciliation(
            campaign_id, proposal_id, candidate_id
        )
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        print("Stop all creation workers and verify the record's origin.")
        phrase = f"reconcile {proposal_id} {evidence['fingerprint']}"
        print(f"To record this matching evidence, type: {phrase}")
        if input("> ").strip() != phrase:
            raise SystemExit("Reconciliation cancelled")
        store.journal.confirm_reconciled(
            proposal_id,
            evidence["fingerprint"],
            evidence["record"],
            reviewed_by=getpass.getuser(),
        )
        print("Reconciliation recorded; fresh approval is required before execution")
    finally:
        await service._writer.aclose()
        await reader.aclose()


def main() -> None:
    """Use OS access to the private file and an interactive exact-content review."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("action", choices=("review", "approve", "revoke", "reconcile"))
    parser.add_argument("proposal_id")
    parser.add_argument("--candidate-id", type=int)
    parser.add_argument("--kind", choices=("creation", "edit"), default="creation")
    args = parser.parse_args()
    if not args.database.is_absolute() or not args.database.exists():
        raise SystemExit("Select the existing absolute private creation database")
    if args.action in ("approve", "reconcile") and not sys.stdin.isatty():
        raise SystemExit("Approval requires an interactive local terminal")
    store = None
    try:
        if args.kind == "edit" and args.candidate_id is not None:
            raise ValueError("An edit can reconcile only its stored target")
        store = (
            EditStore(args.database)
            if args.kind == "edit"
            else LocalApprovals(args.database)
        )
        review = store.review(args.proposal_id)
        if args.action == "reconcile":
            if args.kind == "edit":
                asyncio.run(reconcile_edit(store, args.database, args.proposal_id))
                return
            if args.candidate_id is None:
                raise ValueError("Reconciliation requires an explicit candidate ID")
            asyncio.run(
                reconcile(store, args.database, args.proposal_id, args.candidate_id)
            )
            return
        if args.action == "revoke":
            store.revoke(args.proposal_id)
            print("Proposal revoked")
            return
        print(json.dumps(review, ensure_ascii=False, indent=2))
        if args.action == "approve":
            phrase = f"approve {args.proposal_id} {review['review_digest']}"
            print("Stop other write workers before reapproving a partial run.")
            print(f"To authorize exactly this proposal, type: {phrase}")
            if input("> ").strip() != phrase:
                raise SystemExit("Approval cancelled")
            store.approve(args.proposal_id, review["review_digest"], getpass.getuser())
            print("Approved for five minutes; execution remains a separate action")
    except (PermissionError, ValueError, sqlite3.Error, KankaError):
        raise SystemExit(
            "Local proposal action failed; verify the file, proposal and review"
        ) from None
    except EOFError:
        raise SystemExit("Approval cancelled") from None
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
