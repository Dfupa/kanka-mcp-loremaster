"""Import local TXT/PDF sources into the explicitly configured private store."""

import argparse
import json
import sqlite3
from pathlib import Path

from loremaster.note_models import ImportContext
from loremaster.note_store import NoteStore
from loremaster.note_text import MAX_SOURCE_BYTES


def main() -> None:
    """Ingest exact local bytes without API credentials or campaign mutations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--campaign", required=True, type=int)
    parser.add_argument("--encoding")
    parser.add_argument("--language", default="und")
    parser.add_argument("--locale", choices=("dmy", "mdy", "iso"))
    parser.add_argument("--document-id")
    parser.add_argument("--pdf-start-page", type=int, default=1)
    parser.add_argument("--pdf-page-count", type=int, default=64)
    args = parser.parse_args()
    kind = args.source.suffix.casefold()
    if kind not in (".txt", ".pdf"):
        parser.error("Only TXT and PDF source files are supported")
    if kind == ".pdf" and args.encoding:
        parser.error("PDF extraction does not accept a text encoding override")
    store = None
    try:
        with args.source.open("rb") as source:
            raw = source.read(MAX_SOURCE_BYTES + 1)
        context = ImportContext(language=args.language, locale=args.locale)
        extracted = None
        if kind == ".pdf":
            from loremaster.note_pdf import extract_pdf

            extracted = extract_pdf(
                raw, start_page=args.pdf_start_page, page_count=args.pdf_page_count
            )
        store = NoteStore(args.database)
        row = store.import_bytes(
            args.campaign,
            raw,
            context,
            args.source.name,
            encoding=args.encoding,
            document_id=args.document_id,
            method="pdf" if extracted else "txt",
            extraction=extracted,
        )
        print(
            json.dumps(
                {
                    key: row[key]
                    for key in ("id", "document_id", "source_hash", "version")
                }
                | ({"extraction": row["extraction"]} if extracted else {})
            )
        )
    except (OSError, ValueError, PermissionError) as error:
        parser.error(str(error))
    except sqlite3.Error:
        parser.error("Notes storage is unavailable or invalid")
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
