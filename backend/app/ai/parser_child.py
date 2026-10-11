"""Minimal subprocess entry point for deterministic Resume/Job parsing."""

from __future__ import annotations

import base64
import binascii
import json
import struct
import sys
from dataclasses import asdict
from typing import Any

from app.ai.errors import ResumeParseError
from app.ai.job_parser import JobParseError, parse_job_description
from app.ai.parser_isolation import (
    MAX_JOB_INPUT_CHARS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_RESUME_BYTES,
    MAX_TAXONOMY_ITEMS,
    json_bytes,
)
from app.ai.resume_parser import parse_resume
from app.ai.schemas import TaxonomySkill


def _read_request() -> dict[str, Any]:
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        raise ValueError("Missing parser frame")
    length = struct.unpack(">I", header)[0]
    if length == 0 or length > MAX_REQUEST_BYTES:
        raise ValueError("Oversized parser frame")
    data = sys.stdin.buffer.read(length)
    if len(data) != length:
        raise ValueError("Incomplete parser frame")
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unknown parser protocol")
    return payload


def _parse(request: dict[str, Any]) -> dict[str, Any]:
    rows = request.get("taxonomy")
    if not isinstance(rows, list) or len(rows) > MAX_TAXONOMY_ITEMS:
        raise ValueError("Invalid taxonomy")
    taxonomy = tuple(TaxonomySkill(**row) for row in rows)
    kind = request.get("kind")
    if kind == "resume":
        encoded = request["source_b64"]
        mime_type = request["mime_type"]
        if not isinstance(encoded, str) or not isinstance(mime_type, str):
            raise ValueError("Invalid Resume input")
        if len(encoded) > 4 * ((MAX_RESUME_BYTES + 2) // 3):
            raise ValueError("Oversized Resume input")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid source encoding") from exc
        if len(data) > MAX_RESUME_BYTES:
            raise ValueError("Oversized Resume source")
        parsed = parse_resume(data, mime_type, taxonomy)
    elif kind == "job":
        raw = request["raw_content"]
        if not isinstance(raw, str) or len(raw) > MAX_JOB_INPUT_CHARS:
            raise ValueError("Invalid Job input")
        parsed = parse_job_description(raw, taxonomy)
    else:
        raise ValueError("Unknown parser kind")
    return {"ok": True, "result": asdict(parsed)}


def main() -> int:
    try:
        request = _read_request()
        reply = _parse(request)
    except (ResumeParseError, JobParseError) as exc:
        reply = {"ok": False, "error_kind": exc.kind.value}
    except Exception:
        # No source data, credentials, paths or tracebacks cross IPC.
        reply = {"ok": False, "error_kind": "INTERNAL"}

    try:
        encoded = json_bytes(reply)
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = json_bytes({"ok": False, "error_kind": "INTERNAL"})
        sys.stdout.buffer.write(struct.pack(">I", len(encoded)))
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0
    except (BrokenPipeError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
