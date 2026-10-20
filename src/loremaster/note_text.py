"""Lossless TXT source blocks, conservative quote guards and anchored edits."""

import re
from datetime import date
from html import escape
from typing import Any

from loremaster.note_models import Anchor, NoteAnalysis

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_INLINE_BYTES = 128 * 1024
MAX_SUBMISSION_BYTES = 128 * 1024
BLOCK_BYTES = 16 * 1024
PAGE_BYTES = 128 * 1024


def decode_source(raw: bytes, encoding: str | None = None) -> str:
    """Decode strictly without newline or Unicode normalization."""
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Source must contain 1 byte to 8 MiB")
    selected = encoding
    if selected is None:
        selected = "utf-8-sig"
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            selected = "utf-16"
    try:
        text = raw.decode(selected, errors="strict")
    except (UnicodeError, LookupError):
        raise ValueError("Source decoding failed; provide its encoding") from None
    if not text.strip() or "\x00" in text:
        raise ValueError("Source must be nonblank text without NUL characters")
    return text


def protected_ranges(text: str) -> list[tuple[int, int]]:
    """Protect dialogue and attribution; unmatched opening quotes protect the tail."""
    spans = []
    closers = {'"': '"', "“": "”", "«": "»", "'": "'", "‘": "’"}
    index = 0
    while index < len(text):
        opening = text[index] in closers
        if text[index] == "'" and index and text[index - 1].isalnum():
            opening = False  # Apostrophes within words are not quote boundaries.
        if opening:
            end = text.find(closers[text[index]], index + 1)
            while end >= 0 and (
                text[end - 1] == "\\"
                or text[index] == "'"
                and end + 1 < len(text)
                and text[end + 1].isalnum()
            ):
                end = text.find(closers[text[index]], end + 1)
            end = len(text) if end < 0 else end + 1
            start = text.rfind("\n", 0, index) + 1
            prefix = text[start:index]
            # Keep earlier sentences editable while binding the quote to its speaker.
            boundary = max(prefix.rfind(". "), prefix.rfind("! "), prefix.rfind("? "))
            if boundary >= 0:
                start += boundary + 2
            spans.append((start, end))
            if len(spans) > 8192:
                raise ValueError("Too many dialogue spans; split the source document")
            index = end
        else:
            index += 1
    for match in re.finditer(r"(?m)^[ \t]*[\w .’'-]{1,40}:[^\r\n]*", text):
        spans.append(match.span())
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def source_blocks(text: str) -> list[dict[str, Any]]:
    """Split long lines without losing bytes; map global dialogue into each block."""
    guards = protected_ranges(text)
    blocks = []
    offset = 0
    guard_index = 0
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        while line:
            end = min(len(line), BLOCK_BYTES)
            while len(line[:end].encode("utf-8")) > BLOCK_BYTES:
                end //= 2
            part, line = line[:end], line[end:]
            while guard_index < len(guards) and guards[guard_index][1] <= offset:
                guard_index += 1
            overlaps = []
            for left, right in guards[guard_index:]:
                if left >= offset + end:
                    break
                overlaps.append(
                    [max(left, offset) - offset, min(right, offset + end) - offset]
                )
            blocks.append(
                {
                    "id": f"b-{len(blocks) + 1}",
                    "text": part,
                    "line": number,
                    "offset": offset,
                    "protected": overlaps,
                }
            )
            offset += end
            if len(blocks) > 4096:
                raise ValueError("Too many source blocks; split the source document")
    return blocks


def locate(anchor: Anchor, blocks: dict[str, dict]) -> tuple[int, int]:
    """Resolve an exact anchor uniquely; repeated text needs explicit context."""
    if anchor.block_id not in blocks:
        raise ValueError("Anchor references an unknown source block")
    text = blocks[anchor.block_id]["text"]
    starts = [match.start() for match in re.finditer(re.escape(anchor.excerpt), text)]
    starts = [
        start
        for start in starts
        if text[:start].endswith(anchor.before)
        and text[start + len(anchor.excerpt) :].startswith(anchor.after)
    ]
    if len(starts) != 1:
        raise ValueError("Anchor must match exactly once; supply surrounding context")
    return starts[0], starts[0] + len(anchor.excerpt)


def correct_text(blocks: list[dict], analysis: NoteAnalysis) -> dict[str, str]:
    """Apply nonoverlapping source edits; dialogue and attribution are immutable."""
    lookup = {
        block["id"]: {**block, "protected": list(block["protected"])}
        for block in blocks
    }
    for anchor in analysis.dialogue:
        span = locate(anchor, lookup)
        lookup[anchor.block_id]["protected"].append(span)
    edits: dict[str, list[tuple[int, int, str]]] = {}
    for edit in analysis.edits:
        start, end = locate(edit.anchor, lookup)
        if edit.anchor.block_id not in analysis.analysed_blocks:
            raise ValueError("An edit must belong to an analysed block")
        for left, right in lookup[edit.anchor.block_id]["protected"]:
            if start < right and end > left:
                raise ValueError("An edit intersects protected dialogue/attribution")
        edits.setdefault(edit.anchor.block_id, []).append(
            (start, end, edit.replacement)
        )
    result = {}
    for key, block in lookup.items():
        changes = sorted(edits.get(key, []))
        if any(a[1] > b[0] for a, b in zip(changes, changes[1:], strict=False)):
            raise ValueError("Source edits overlap")
        value = block["text"]
        for start, end, replacement in reversed(changes):
            value = value[:start] + replacement + value[end:]
        result[key] = value
    return result


def session_date(anchor: Anchor | None, blocks: dict, locale: str | None) -> str | None:
    """Parse an evidenced real date; leave ambiguous and fantasy dates unknown."""
    if anchor is None:
        return None
    locate(anchor, blocks)
    value = anchor.excerpt
    iso = re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
    try:
        if iso:
            return date.fromisoformat(value).isoformat()
        match = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", value)
        if match:
            first, second, year = map(int, match.groups())
            if locale == "dmy" or first > 12 and second <= 12:
                return date(year, second, first).isoformat()
            if locale == "mdy" or second > 12 and first <= 12:
                return date(year, first, second).isoformat()
    except ValueError:
        pass
    return None


def html_text(text: str) -> str:
    """Escape source content and preserve visual line breaks."""
    return "<p>" + escape(text, quote=False).replace("\n", "<br>\n") + "</p>"


def display_blocks(blocks: list[dict], corrected: dict[str, str] | None = None) -> str:
    """Present PDF page breaks outside source spans; TXT remains an exact join."""
    parts = []
    previous_page = None
    for block in blocks:
        page = block.get("page")
        if previous_page is not None and page != previous_page:
            parts.append("\n\n")
        parts.append(corrected[block["id"]] if corrected is not None else block["text"])
        previous_page = page
    return "".join(parts)
