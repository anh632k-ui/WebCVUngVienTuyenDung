from __future__ import annotations

import io
import tomllib
from importlib.metadata import version
from pathlib import Path

from packaging.version import Version
from reportlab.pdfgen import canvas

from app.ai.pdf_docx_extractor import extract_pdf_text


def test_pdf_parser_requires_stream_work_hardening_release() -> None:
    metadata = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert "pypdf>=6.20.0,<7.0" in metadata["project"]["dependencies"]

    installed = Version(version("pypdf"))
    assert Version("6.20.0") <= installed < Version("7.0.0")


def test_hardened_pdf_parser_still_extracts_normal_resume_text() -> None:
    source = io.BytesIO()
    document = canvas.Canvas(source, pagesize=(612, 792))
    document.drawString(72, 720, "Candidate CV Python PostgreSQL FastAPI")
    document.showPage()
    document.save()

    extracted = extract_pdf_text(source.getvalue())
    assert "Candidate CV Python PostgreSQL FastAPI" in extracted
