"""Development-only access to the isolated creation rehearsal."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

spec = spec_from_file_location(
    "offline_creation",
    Path(__file__).resolve().parents[1] / "scripts" / "offline_creation.py",
)
if spec is None or spec.loader is None:
    raise RuntimeError("Offline creation fixture is unavailable")
offline = module_from_spec(spec)
spec.loader.exec_module(offline)


def approved(rehearsal, draft):
    """Act as the trusted reviewer only inside the mock campaign."""
    proposal = rehearsal.prepare(draft)
    return rehearsal.gate.approve(
        proposal["proposal_id"],
        reviewed_digest=proposal["content_digest"],
        approved_by="offline-test-reviewer",
    )
