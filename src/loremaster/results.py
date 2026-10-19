"""The shared, version 0.1 MCP result contract."""

from typing import Any, TypedDict


class MissingData(TypedDict):
    """An endpoint whose expected data could not be included."""

    source: str
    reason: str


class ErrorInfo(TypedDict):
    """A sanitized failure; upstream bodies and credentials are never included."""

    code: str
    message: str
    status_code: int | None


class ToolResult(TypedDict):
    """A read result with explicit scope, source, completeness, and failure state.

    ``complete`` requires all collection pages and linked data in this response.
    It makes no claim about records hidden by the configured token's permissions.
    ``next_page`` concerns the primary collection, not missing linked records.
    """

    data: Any
    campaign_id: int | None
    entity_id: int | None
    source: str
    complete: bool
    page: int | None
    next_page: int | None
    missing_data: list[MissingData]
    error: ErrorInfo | None
