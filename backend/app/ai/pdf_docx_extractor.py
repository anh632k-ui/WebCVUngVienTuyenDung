from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from typing import Any, cast
from xml.etree import ElementTree

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
MAX_DOCX_RELATIONSHIPS_BYTES = 64 * 1024
# Keep parser acceptance aligned with the upload validator's document-part guard.
MAX_DOCX_DOCUMENT_XML_BYTES = 16 * 1024 * 1024
DOCX_XML_CHUNK_SIZE = 64 * 1024
DOCX_RELATIONSHIPS_PATH = "_rels/.rels"
DOCX_DOCUMENT_PATH = "word/document.xml"
DOCX_TRANSITIONAL_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
DOCX_STRICT_RELATIONSHIP = "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"
DOCX_TRANSITIONAL_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCX_STRICT_NAMESPACE = "http://purl.oclc.org/ooxml/wordprocessingml/main"
DOCX_PROFILES = {
    DOCX_TRANSITIONAL_RELATIONSHIP: DOCX_TRANSITIONAL_NAMESPACE,
    DOCX_STRICT_RELATIONSHIP: DOCX_STRICT_NAMESPACE,
}
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


def _iter_bounded_zip_entry(
    archive: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
    maximum_bytes: int,
) -> Iterator[bytes]:
    if entry.file_size > maximum_bytes:
        raise ResumeParseError(
            ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
            f"DOCX package part {entry.filename!r} exceeds its size limit",
        )
    bytes_read = 0
    with archive.open(entry) as source:
        while True:
            remaining = maximum_bytes - bytes_read
            chunk = source.read(min(DOCX_XML_CHUNK_SIZE, remaining + 1))
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > maximum_bytes:
                raise ResumeParseError(
                    ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
                    f"DOCX package part {entry.filename!r} exceeds its size limit",
                )
            yield chunk


def _document_root_namespace(
    archive: zipfile.ZipFile,
    document: zipfile.ZipInfo,
) -> str:
    parser = ElementTree.XMLPullParser(events=("start", "end"))
    root_namespace: str | None = None
    for chunk in _iter_bounded_zip_entry(
        archive,
        document,
        MAX_DOCX_DOCUMENT_XML_BYTES,
    ):
        parser.feed(chunk)
        events = cast(Iterator[tuple[str, Any]], parser.read_events())
        for event, element in events:
            if root_namespace is None:
                if event != "start" or not isinstance(element.tag, str):
                    raise ResumeParseError(
                        ResumeParseErrorKind.UNREADABLE_SOURCE,
                        "DOCX document XML has an invalid root element",
                    )
                namespace, separator, local_name = element.tag[1:].partition("}")
                if not element.tag.startswith("{") or separator != "}" or local_name != "document":
                    raise ResumeParseError(
                        ResumeParseErrorKind.UNREADABLE_SOURCE,
                        "DOCX document XML has an invalid root element",
                    )
                root_namespace = namespace
            if event == "end":
                element.clear()
    parser.close()
    if root_namespace is None:
        raise ResumeParseError(
            ResumeParseErrorKind.UNREADABLE_SOURCE,
            "DOCX document XML has no root element",
        )
    return root_namespace


def _detect_docx_profile(source_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
        relationships = archive.getinfo(DOCX_RELATIONSHIPS_PATH)
        document = archive.getinfo(DOCX_DOCUMENT_PATH)
        relationship_xml = b"".join(
            _iter_bounded_zip_entry(
                archive,
                relationships,
                MAX_DOCX_RELATIONSHIPS_BYTES,
            )
        )
        relationship_root = ElementTree.fromstring(relationship_xml)
        relationship_types: set[str] = set()
        for element in relationship_root:
            if element.tag.rsplit("}", 1)[-1] != "Relationship":
                continue
            if element.attrib.get("Target", "").lstrip("/") != DOCX_DOCUMENT_PATH:
                continue
            if element.attrib.get("TargetMode", "Internal") != "Internal":
                raise ResumeParseError(
                    ResumeParseErrorKind.UNREADABLE_SOURCE,
                    "DOCX main document relationship must be internal",
                )
            relationship_type = element.attrib.get("Type")
            if relationship_type is not None:
                relationship_types.add(relationship_type)

        if len(relationship_types) != 1:
            raise ResumeParseError(
                ResumeParseErrorKind.UNREADABLE_SOURCE,
                "DOCX main document profile is missing or ambiguous",
            )
        relationship_type = relationship_types.pop()
        expected_namespace = DOCX_PROFILES.get(relationship_type)
        document_namespace = _document_root_namespace(archive, document)
        if expected_namespace is None or document_namespace != expected_namespace:
            raise ResumeParseError(
                ResumeParseErrorKind.UNREADABLE_SOURCE,
                "DOCX relationship and document XML profiles do not match",
            )
        return document_namespace


def _extract_strict_docx_text(source_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
        document = archive.getinfo(DOCX_DOCUMENT_PATH)
        parser = ElementTree.XMLPullParser(events=("start", "end"))
        paragraph_parts: list[str] | None = None
        paragraph_count = 0
        parts: list[str] = []
        size = 0
        paragraph_tag = f"{{{DOCX_STRICT_NAMESPACE}}}p"
        text_tag = f"{{{DOCX_STRICT_NAMESPACE}}}t"
        tab_tag = f"{{{DOCX_STRICT_NAMESPACE}}}tab"
        break_tags = {
            f"{{{DOCX_STRICT_NAMESPACE}}}br",
            f"{{{DOCX_STRICT_NAMESPACE}}}cr",
        }

        for chunk in _iter_bounded_zip_entry(
            archive,
            document,
            MAX_DOCX_DOCUMENT_XML_BYTES,
        ):
            parser.feed(chunk)
            events = cast(Iterator[tuple[str, Any]], parser.read_events())
            for event, element in events:
                if event == "start" and element.tag == paragraph_tag:
                    if paragraph_parts is not None:
                        raise ResumeParseError(
                            ResumeParseErrorKind.UNREADABLE_SOURCE,
                            "Strict DOCX contains nested paragraphs",
                        )
                    paragraph_count += 1
                    if paragraph_count > MAX_DOCX_BLOCKS:
                        raise ResumeParseError(
                            ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
                            "DOCX contains too many text blocks",
                        )
                    paragraph_parts = []
                elif event == "end" and paragraph_parts is not None:
                    if element.tag == text_tag:
                        paragraph_parts.append(element.text or "")
                    elif element.tag == tab_tag:
                        paragraph_parts.append("\t")
                    elif element.tag in break_tags:
                        paragraph_parts.append("\n")
                    elif element.tag == paragraph_tag:
                        text = "".join(paragraph_parts).strip()
                        if text:
                            size = _guard_text_growth(parts, size, text)
                        paragraph_parts = None
                if event == "end":
                    element.clear()
        parser.close()
        return require_meaningful_text("\n".join(parts))


def extract_docx_text(source_bytes: bytes) -> str:
    try:
        _preflight_docx_package(source_bytes)
        profile = _detect_docx_profile(source_bytes)
        if profile == DOCX_STRICT_NAMESPACE:
            return _extract_strict_docx_text(source_bytes)
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
