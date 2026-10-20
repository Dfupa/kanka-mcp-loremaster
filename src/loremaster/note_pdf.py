"""Optional local PDF extraction in a bounded worker without Kanka credentials."""

import hashlib
import importlib.util
import json
import logging
import math
import os
import subprocess
import sys
from collections import Counter
from io import BytesIO
from typing import Any

from loremaster.note_text import MAX_SOURCE_BYTES, protected_ranges, source_blocks

MAX_PDF_PAGES = 4096
MAX_SELECTED_PAGES = 256
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024
WORKER_SECONDS = 30


def _range(start_page: int, page_count: int) -> None:
    if (
        type(start_page) is not int
        or start_page < 1
        or type(page_count) is not int
        or not 1 <= page_count <= MAX_SELECTED_PAGES
    ):
        raise ValueError("PDF start page must be positive; page count must be 1–256")


def extract_pdf(raw: bytes, *, start_page: int = 1, page_count: int = 64) -> dict:
    """Extract a bounded page range without network, OCR or in-process PDF parsing.

    Original bytes remain the caller's source. Errors never include PDF payloads
    or worker logs. Missing/failed pages are explicit; positions are approximate.
    """
    _range(start_page, page_count)
    if not raw or len(raw) > MAX_SOURCE_BYTES or not raw.startswith(b"%PDF-"):
        raise ValueError("PDF must have a valid header and contain at most 8 MiB")
    if importlib.util.find_spec("pypdf") is None:
        raise ValueError("PDF support requires installation with the [pdf] extra")
    environment = {k: v for k, v in os.environ.items() if not k.startswith("KANKA_")}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loremaster.note_pdf",
                "--worker",
                str(start_page),
                str(page_count),
            ],
            input=raw,
            capture_output=True,
            timeout=WORKER_SECONDS,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(
            "PDF extraction timed out; select a smaller page range"
        ) from None
    if result.returncode != 0 or len(result.stdout) > MAX_RESULT_BYTES:
        raise ValueError("PDF worker exceeded a resource limit or failed")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise ValueError("PDF worker returned an invalid result") from None
    if not isinstance(payload, dict):
        raise ValueError("PDF worker returned an invalid result")
    if payload.get("error"):
        message = {
            "encrypted": "PDF is encrypted; provide a decrypted local copy",
            "page_range": "PDF start page is outside the document",
            "page_limit": "PDF contains too many pages",
        }.get(str(payload["error"]), "PDF could not be parsed within extraction limits")
        raise ValueError(message)
    if (
        not isinstance(payload.get("blocks"), list)
        or not isinstance(payload.get("metadata"), dict)
        or payload["metadata"].get("source_sha256") != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("PDF worker returned mismatched source provenance")
    return payload


def _images(
    resources: Any, depth: int = 0, seen: set | None = None
) -> tuple[bool, bool]:
    """Inspect resource metadata without decoding images or following actions."""
    seen = set() if seen is None else seen
    if depth > 8 or len(seen) > 128:
        return False, False
    if not resources:
        return False, True
    resources = resources.get_object()
    objects = resources.get("/XObject", {})
    objects = objects.get_object() if hasattr(objects, "get_object") else objects
    found, complete = False, True
    for reference in objects.values():
        identity = (
            getattr(reference, "idnum", None),
            getattr(reference, "generation", None),
        )
        if identity == (None, None):
            identity = id(reference)
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > 128:
            return found, False
        obj = reference.get_object()
        if obj.get("/Subtype") == "/Image":
            found = True
        elif obj.get("/Subtype") == "/Form":
            nested, checked = _images(obj.get("/Resources"), depth + 1, seen)
            found, complete = found or nested, complete and checked
    return found, complete


def _content_size(page) -> int:
    """Bound decompressed content before the text interpreter, inside the worker."""
    contents = page.get("/Contents")
    if contents is None:
        return 0
    contents = contents.get_object()
    streams = contents if isinstance(contents, list) else [contents]
    size = 0
    if len(streams) > 128:
        raise ValueError("content_limit")
    for stream in streams:
        stream = stream.get_object()
        if stream.get("/F"):
            raise ValueError("external_content")
        size += len(stream.get_data())
        if size > MAX_CONTENT_BYTES:
            raise ValueError("content_limit")
    return size


def _page(page, number: int) -> dict:
    """Keep extractor output verbatim, with quality flags and baseline positions."""
    warnings, fragments = [], []
    try:
        size = _content_size(page)
        has_images, checked = _images(page.get("/Resources"))
        if not checked:
            warnings.append("image_resource_inspection_incomplete")
        positions_complete = True

        def visitor(text, cm, tm, font, font_size):
            nonlocal positions_complete
            if not text:
                return
            if len(fragments) >= 4096:
                positions_complete = False
                return
            x = float(tm[4] * cm[0] + tm[5] * cm[2] + cm[4])
            y = float(tm[4] * cm[1] + tm[5] * cm[3] + cm[5])
            values = [x, y, float(font_size)]
            if not all(math.isfinite(v) and abs(v) < 1e8 for v in values):
                positions_complete = False
                return
            fragments.append(
                {"text": text, "x": x, "y": y, "font_size": float(font_size)}
            )
            if font is None and text.strip():
                warnings.append("font_mapping_uncertain")
            if tm[1] or tm[2]:
                warnings.append("rotated_text")

        text = page.extract_text(visitor_text=visitor)
        if len(text.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("text_limit")
        if "\ufffd" in text or "\x00" in text or "(cid:" in text:
            warnings.append("glyph_mapping_uncertain")
        if has_images:
            warnings.append(
                "image_only_ocr_required"
                if not text.strip()
                else "mixed_content_requires_review"
            )
        if not text.strip() and not has_images:
            warnings.append("no_extractable_text")
        if page.rotation:
            warnings.append("rotated_page")
        positions_complete = (
            positions_complete and "".join(f["text"] for f in fragments) == text
        )
        if not positions_complete:
            warnings.append("position_mapping_incomplete")
        width = float(page.mediabox.width)
        xs = [f["x"] for f in fragments if f["text"].strip()]
        if len(xs) > 1 and max(xs) - min(xs) > width * 0.3:
            warnings.append("reading_order_uncertain")
        status = (
            "text"
            if text.strip()
            else "ocr_required"
            if has_images
            else "no_extractable_text"
        )
        if text.strip() and warnings:
            status = "text_requires_review"
        return {
            "page": number,
            "text": text,
            "status": status,
            "warnings": sorted(set(warnings)),
            "has_images": has_images,
            "positions_complete": positions_complete,
            "fragments": fragments,
            "content_bytes": size,
            "rotation": page.rotation,
        }
    except MemoryError:
        raise
    except Exception:
        # Parser exceptions may contain private PDF content; expose only a code.
        return {
            "page": number,
            "text": "",
            "status": "extraction_failed",
            "warnings": ["page_extraction_failed"],
            "has_images": False,
            "positions_complete": False,
            "fragments": [],
        }


def _assemble(pages: list[dict], metadata: dict) -> dict:
    """Map pages/fragments to ordinary source blocks without deleting headers."""
    margins = Counter()
    for page in pages:
        lines = page["text"].splitlines()
        margins.update(
            {line.strip() for line in [*lines[:2], *lines[-2:]] if line.strip()}
        )
    joined = "\n".join(p["text"] for p in pages)
    guards = protected_ranges(joined)
    blocks, offset, virtual_offset = [], 0, 0
    for page in pages:
        text = page["text"]
        local = (
            source_blocks(text)
            if text
            else [{"id": "empty", "text": "", "line": 1, "offset": 0, "protected": []}]
        )
        cursor = 0
        positioned = []
        if page["positions_complete"]:
            for fragment in page["fragments"]:
                positioned.append((cursor, cursor + len(fragment["text"]), fragment))
                cursor += len(fragment["text"])
        for block in local:
            start, end = block["offset"], block["offset"] + len(block["text"])
            block["protected"] = [
                [
                    max(a, start + virtual_offset) - start - virtual_offset,
                    min(b, end + virtual_offset) - start - virtual_offset,
                ]
                for a, b in guards
                if a < end + virtual_offset and b > start + virtual_offset
            ]
            block.update(
                id=f"p-{page['page']}-{block['id']}",
                page=page["page"],
                page_offset=start,
                offset=offset + start,
                readable=bool(text.strip())
                and "glyph_mapping_uncertain" not in page["warnings"],
                quote_fidelity="extracted_text_only",
                regions=[
                    {k: fragment[k] for k in ("x", "y", "font_size")}
                    for a, b, fragment in positioned
                    if a < end and b > start
                ],
                warnings=list(page["warnings"]),
            )
            if margins[block["text"].strip()] > 1:
                block["warnings"].append("repeated_margin_text_candidate")
            if len(block["regions"]) > 64:
                block["regions"] = block["regions"][:64]
                block["warnings"].append("region_mapping_incomplete")
            blocks.append(block)
            if len(blocks) > 4096:
                raise ValueError("block_limit")
        offset += len(text)
        virtual_offset += len(text) + 1
    summaries = [
        {k: v for k, v in page.items() if k not in ("text", "fragments")}
        | {"text_sha256": hashlib.sha256(page["text"].encode()).hexdigest()}
        for page in pages
    ]
    metadata.update(
        pages=summaries,
        quote_fidelity="extracted_text_only",
        visual_review_required=True,
        complete=not metadata["omitted_ranges"]
        and all(p["status"] == "text" for p in pages),
    )
    metadata["ocr_requests"] = [
        {
            "page": p["page"],
            "source_sha256": metadata["source_sha256"],
            "reason": p["status"],
            "execution": "not_configured",
        }
        for p in pages
        if p["has_images"] or not p["text"].strip()
    ]
    return {"blocks": blocks, "metadata": metadata}


def _extract(raw: bytes, start: int, count: int) -> dict:
    """Worker implementation; call extract_pdf for timeout/memory isolation."""
    from pypdf import PdfReader, __version__

    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted:
        return {"error": "encrypted"}
    total = len(reader.pages)
    if total > MAX_PDF_PAGES:
        return {"error": "page_limit"}
    if start > total:
        return {"error": "page_range"}
    end = min(total, start + count - 1)
    pages = [
        _page(reader.pages[number - 1], number) for number in range(start, end + 1)
    ]
    if sum(len(p["text"].encode()) for p in pages) > MAX_SOURCE_BYTES:
        raise ValueError("text_limit")
    omitted = ([[1, start - 1]] if start > 1 else []) + (
        [[end + 1, total]] if end < total else []
    )
    return _assemble(
        pages,
        {
            "format": "pdf",
            "extractor": "pypdf",
            "extractor_version": __version__,
            "adapter_version": 1,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "page_count": total,
            "start_page": start,
            "end_page": end,
            "page_limit": count,
            "next_page": end + 1 if end < total else None,
            "omitted_ranges": omitted,
        },
    )


def _worker() -> None:
    """Fixed local worker protocol; no shell, Kanka token, provider or PDF actions."""
    logging.disable(logging.CRITICAL)
    limits = False
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        limits = True
    except (ImportError, OSError, ValueError):
        pass
    try:
        raw = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
        start, count = int(sys.argv[2]), int(sys.argv[3])
        _range(start, count)
        if not raw.startswith(b"%PDF-") or len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("invalid_source")
        result = _extract(raw, start, count)
        if "metadata" in result:
            result["metadata"]["memory_cpu_limits_enforced"] = limits
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_RESULT_BYTES:
            raise ValueError("result_limit")
        sys.stdout.buffer.write(encoded)
    except Exception:
        sys.stdout.write('{"error":"parse_failed"}')


if __name__ == "__main__" and sys.argv[1:2] == ["--worker"]:
    _worker()
