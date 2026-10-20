"""Small invented PDFs; no downloaded documents or additional fixture libraries."""

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)


def pdf_bytes(pages, *, encrypted=False):
    """Build text, column, image-only and mixed pages using standard PDF objects."""
    writer = PdfWriter()
    for spec in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
        resources = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        content = b""
        for line in spec.get("lines", []):
            x, y, text = line
            escaped = (
                text.encode("cp1252")
                .replace(b"\\", b"\\\\")
                .replace(b"(", b"\\(")
                .replace(b")", b"\\)")
            )
            content += f"BT /F1 12 Tf {x} {y} Td (".encode() + escaped + b") Tj ET\n"
        if spec.get("image"):
            image = DecodedStreamObject()
            image.set_data(b"\xff\xff\xff")
            image.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(1),
                    NameObject("/Height"): NumberObject(1),
                    NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                }
            )
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/Im0"): writer._add_object(image)}
            )
            content += b"q 100 0 0 100 72 100 cm /Im0 Do Q\n"
        stream = DecodedStreamObject()
        stream.set_data(spec.get("content", content))
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = writer._add_object(stream)
        if spec.get("rotation"):
            page.rotate(spec["rotation"])
        if spec.get("missing_font"):
            resources[NameObject("/Font")] = DictionaryObject()
        if spec.get("action"):
            page[NameObject("/Annots")] = ArrayObject(
                [
                    writer._add_object(
                        DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Annot"),
                                NameObject("/Subtype"): NameObject("/Link"),
                                NameObject("/A"): DictionaryObject(
                                    {
                                        NameObject("/S"): NameObject("/JavaScript"),
                                        NameObject("/JS"): NameObject("/NoExecution"),
                                    }
                                ),
                            }
                        )
                    )
                ]
            )
    if encrypted:
        writer.encrypt("fixture-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def digital_pdf():
    return pdf_bytes(
        [
            {
                "lines": [
                    (72, 740, "Sesión 12 — 17/10/2026"),
                    (72, 715, "L llego al puente."),
                    (72, 690, "V dijo: «no pienso bolver»."),
                ]
            },
            {
                "lines": [
                    (72, 740, "Sesión 13 — 19/10/2026"),
                    (72, 715, "Mara nos enseño el paso."),
                ]
            },
        ]
    )
