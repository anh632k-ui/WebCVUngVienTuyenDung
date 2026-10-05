from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.ai.errors import ResumeParseError, ResumeParseErrorKind
from app.ai.text_processing import MAX_EXTRACTED_TEXT_CHARS, require_meaningful_text

PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_DOCX_BLOCKS = 20_000
MAX_PDF_PAGES = 1_000


def _guard_text_growth(parts: list[str], current_size: int, value: str) -> int:
    new_size = current_size + len(value) + (2 if parts else 0)
    if new_size > MAX_EXTRACTED_TEXT_CHARS:
        raise ResumeParseError(
            ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
            "Extracted resume text exceeds the parser limit",
        )
    parts.append(value)
    return new_size


def extract_pdf_text(source_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(source_bytes), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ResumeParseError(
                ResumeParseErrorKind.ENCRYPTED_PDF,
                "Encrypted PDF cannot be read without a password",
            )
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ResumeParseError(
                ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
                "PDF contains too many pages",
            )
        parts: list[str] = []
        size = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                size = _guard_text_growth(parts, size, page_text.strip())
        return require_meaningful_text("\n\n".join(parts))
    except ResumeParseError:
        raise
    except Exception as error:
        raise ResumeParseError(
            ResumeParseErrorKind.UNREADABLE_SOURCE,
            "PDF source is corrupt or unreadable",
        ) from error


def _iter_docx_blocks(document: DocumentObject) -> Iterator[str]:
    block_count = 0
    body: Any = document.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text.strip()
            block_count += 1
            if block_count > MAX_DOCX_BLOCKS:
                raise ResumeParseError(
                    ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
                    "DOCX contains too many text blocks",
                )
            yield text
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row in table.rows:
                for cell in row.cells:
                    text = "\n".join(
                        paragraph.text.strip()
                        for paragraph in cell.paragraphs
                        if paragraph.text.strip()
                    )
                    if text:
                        block_count += 1
                        if block_count > MAX_DOCX_BLOCKS:
                            raise ResumeParseError(
                                ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
                                "DOCX contains too many text blocks",
                            )
                        yield text


def extract_docx_text(source_bytes: bytes) -> str:
    try:
        document = Document(io.BytesIO(source_bytes))
        parts: list[str] = []
        size = 0
        for block in _iter_docx_blocks(document):
            size = _guard_text_growth(parts, size, block)
        return require_meaningful_text("\n".join(parts))
    except ResumeParseError:
        raise
    except Exception as error:
        raise ResumeParseError(
            ResumeParseErrorKind.UNREADABLE_SOURCE,
            "DOCX source is corrupt or unreadable",
        ) from error


def extract_resume_text(source_bytes: bytes, mime_type: str) -> str:
    if mime_type == PDF_MIME_TYPE:
        return extract_pdf_text(source_bytes)
    if mime_type == DOCX_MIME_TYPE:
        return extract_docx_text(source_bytes)
    raise ResumeParseError(
        ResumeParseErrorKind.UNSUPPORTED_MIME_TYPE,
        "Resume MIME type is not supported by the parser",
    )
