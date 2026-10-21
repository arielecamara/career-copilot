"""DOCX and single-column text PDF readers.

Multi-column files are refused. Flattening them would invent a reading order.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Literal, cast

from career_copilot.dates import find_date_range
from career_copilot.errors import ParseError, UnsupportedLayoutError
from career_copilot.layout import (
    is_bullet_text,
    is_multi_column,
    merge_wrapped_lines,
    strip_bullet,
    words_to_visual_lines,
)
from career_copilot.models import BlockKind, DocumentKind, DocumentRecord, TextBlock
from career_copilot.vocab import looks_like_heading


def parse_document(path: Path, kind: DocumentKind) -> DocumentRecord:
    if not path.is_file():
        raise ParseError(f"File not found: {path.name}")
    suffix = path.suffix.casefold()
    if suffix == ".docx":
        return parse_docx(path, kind)
    if suffix == ".pdf":
        return parse_pdf(path, kind)
    raise ParseError("Supported files are .docx and single-column text .pdf.")


def parse_docx(path: Path, kind: DocumentKind) -> DocumentRecord:
    data = path.read_bytes()
    if not data:
        raise ParseError("The file is empty.")
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover
        raise ParseError("python-docx is not installed.") from exc

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError("Could not read this file as a DOCX.") from exc

    for section in document.sections:
        if _column_count(section, qn) > 1:
            raise UnsupportedLayoutError(
                "This DOCX uses more than one column. Export a single-column DOCX and try again."
            )

    blocks: list[TextBlock] = []
    seen_cells: set[int] = set()
    for item in _iter_body(document, Paragraph, Table):
        paragraphs = [item] if isinstance(item, Paragraph) else _table_paragraphs(item, seen_cells)
        for paragraph in paragraphs:
            block = _paragraph_block(paragraph)
            if block is not None:
                blocks.append(block)
    if not blocks:
        raise ParseError("This DOCX has no text.")
    return _record(path, data, "docx", kind, blocks, [])


def parse_pdf(path: Path, kind: DocumentKind) -> DocumentRecord:
    data = path.read_bytes()
    if not data:
        raise ParseError("The file is empty.")
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ParseError("pdfplumber is not installed.") from exc

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        message = str(exc).casefold()
        if "password" in message or "encrypt" in message:
            raise ParseError("This PDF is encrypted. Export an unencrypted text PDF.") from exc
        raise ParseError("Could not read this file as a PDF.") from exc

    blocks: list[TextBlock] = []
    empty_pages = 0
    try:
        if not pdf.pages:
            raise ParseError("This PDF has no pages.")
        for index, page in enumerate(pdf.pages, start=1):
            words = page.extract_words() or []
            if not words:
                empty_pages += 1
                continue
            width = float(page.width)
            if is_multi_column(words, width):
                raise UnsupportedLayoutError(
                    f"Page {index} is not a single column. "
                    "Export a single-column text PDF and try again."
                )
            visual = words_to_visual_lines(words)
            if not visual:
                empty_pages += 1
                continue
            content_right = max(line.x1 for line in visual)
            blocks.extend(merge_wrapped_lines(visual, content_right))
    finally:
        pdf.close()

    if not blocks:
        raise ParseError(
            "No text layer found. This parser reads single-column text PDFs. "
            "Scanned pages are not supported."
        )
    warnings: list[str] = []
    if empty_pages:
        warnings.append(f"Skipped {empty_pages} page(s) with no text layer.")
    return _record(path, data, "pdf", kind, blocks, warnings)


def _record(
    path: Path,
    data: bytes,
    media_type: str,
    kind: DocumentKind,
    blocks: list[TextBlock],
    warnings: list[str],
) -> DocumentRecord:
    digest = hashlib.sha256(data).hexdigest()
    return DocumentRecord(
        document_id="doc_" + digest[:16],
        filename=path.name,
        media_type=cast(Literal["docx", "pdf"], media_type),
        kind=kind,
        sha256=digest,
        single_column=True,
        blocks=blocks,
        warnings=warnings,
    )


def _column_count(section: object, qn: object) -> int:
    sect_pr = section._sectPr  # type: ignore[attr-defined]
    counts = [1]
    for cols in sect_pr.findall(qn("w:cols")):  # type: ignore[operator]
        raw = cols.get(qn("w:num"))  # type: ignore[operator]
        if raw is not None:
            try:
                counts.append(int(raw))
            except ValueError:
                continue
        children = list(cols.findall(qn("w:col")))  # type: ignore[operator]
        if len(children) > 1:
            counts.append(len(children))
    return max(counts)


def _iter_body(document: object, paragraph_type: type, table_type: type):
    from docx.oxml.ns import qn

    body = document.element.body  # type: ignore[attr-defined]
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield paragraph_type(child, document)
        elif child.tag == qn("w:tbl"):
            yield table_type(child, document)


def _table_paragraphs(table: object, seen_cells: set[int]) -> list[object]:
    paragraphs = []
    for row in table.rows:  # type: ignore[attr-defined]
        for cell in row.cells:
            marker = id(cell._tc)
            if marker in seen_cells:
                continue
            seen_cells.add(marker)
            paragraphs.extend(cell.paragraphs)
    return paragraphs


def _paragraph_block(paragraph: object) -> TextBlock | None:
    text = str(paragraph.text).strip()  # type: ignore[attr-defined]
    if not text:
        return None
    style_name = ""
    style = paragraph.style  # type: ignore[attr-defined]
    if style is not None and style.name:
        style_name = str(style.name)
    bullet = _is_bullet_paragraph(paragraph, text, style_name)
    if _is_heading_paragraph(text, style_name, bullet):
        return TextBlock(text=text, kind=BlockKind.heading)
    if bullet:
        body = strip_bullet(text)
        if not body:
            return None
        return TextBlock(text=body, kind=BlockKind.bullet)
    return TextBlock(text=text, kind=BlockKind.paragraph)


def _is_heading_paragraph(text: str, style_name: str, bullet: bool) -> bool:
    if bullet:
        return False
    if style_name.startswith("Heading"):
        return True
    if find_date_range(text) is not None or "|" in text:
        return False
    return looks_like_heading(text)


def _is_bullet_paragraph(paragraph: object, text: str, style_name: str) -> bool:
    properties = paragraph._p.pPr  # type: ignore[attr-defined]
    if properties is not None and properties.numPr is not None:
        return True
    if style_name.startswith("List"):
        return True
    return is_bullet_text(text)
