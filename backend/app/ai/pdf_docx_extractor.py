from __future__ import annotations

import io
import zipfile
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
# Package-level guards run before python-docx can inflate OPC parts. These are
# generous for a <=5 MiB resume while bounding highly-compressed ZIP packages.
MAX_DOCX_ZIP_ENTRIES = 2_048
MAX_DOCX_UNCOMPRESSED_PACKAGE_BYTES = 64 * 1024 * 1024
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


def _preflight_docx_package(source_bytes: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
            total_uncompressed_bytes = 0
            for entry_count, entry in enumerate(archive.infolist(), start=1):
                if entry_count > MAX_DOCX_ZIP_ENTRIES:
                    raise ResumeParseError(
                        ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
                        "DOCX package contains too many ZIP entries",
                    )
                if entry.file_size < 0 or entry.compress_size < 0:
                    raise ResumeParseError(
                        ResumeParseErrorKind.UNREADABLE_SOURCE,
                        "DOCX package metadata is invalid",
                    )
                if entry.flag_bits & 0x1:
                    raise ResumeParseError(
                        ResumeParseErrorKind.UNREADABLE_SOURCE,
                        "Encrypted DOCX package entries are not supported",
                    )
                total_uncompressed_bytes += entry.file_size
                if total_uncompressed_bytes > MAX_DOCX_UNCOMPRESSED_PACKAGE_BYTES:
                    raise ResumeParseError(
                        ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
                        "DOCX package exceeds the uncompressed size limit",
                    )
    except ResumeParseError:
        raise
    except (NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ResumeParseError(
            ResumeParseErrorKind.UNREADABLE_SOURCE,
            "DOCX source is corrupt or unreadable",
        ) from error


def extract_docx_text(source_bytes: bytes) -> str:
    try:
        _preflight_docx_package(source_bytes)
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
