"""Bounded parser subprocess bridge; no database, broker or embedding imports."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys
from contextlib import suppress
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from app.ai.errors import ResumeParseError, ResumeParseErrorKind
from app.ai.job_parser import JobParseError, JobParseErrorKind
from app.ai.job_schemas import ParsedJobResult, ParsedJobSkill
from app.ai.schemas import (
    ParsedEducation,
    ParsedExperience,
    ParsedProfile,
    ParsedResumeResult,
    ParsedSkill,
    TaxonomySkill,
)
from app.ai.text_processing import MAX_EXTRACTED_TEXT_CHARS

# Resume uploads are capped at 5 MiB by the API; IPC includes base64 overhead.
MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 9 * 1024 * 1024
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_TAXONOMY_ITEMS = 4096
DEFAULT_PARSER_TIMEOUT_SECONDS = 120.0
MAX_JOB_INPUT_CHARS = 1_000_000
_FRAME_BYTES = 4
_backend_dir = Path(__file__).resolve().parents[2]
_CHILD_SEMAPHORE = asyncio.Semaphore(1)


class ParserIsolationError(RuntimeError):
    """Controlled infrastructure/protocol error, never includes source content."""


class ParserIsolationTimeout(ParserIsolationError):
    """The child exceeded its bounded execution deadline."""


def json_bytes(payload: dict[str, Any]) -> bytes:
    def encode_other(value: object) -> str:
        if isinstance(value, (date, Decimal)):
            return str(value)
        raise TypeError("Unsupported parser result type")

    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), default=encode_other, allow_nan=False
    ).encode("utf-8")


def _parser_environment() -> dict[str, str]:
    # Do not pass DATABASE_URL, broker URLs, JWT secrets or application env settings.
    allowed = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    )
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result["PYTHONPATH"] = str(_backend_dir)
    result["PYTHONNOUSERSITE"] = "1"
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    result["PYTHONIOENCODING"] = "utf-8"
    return result


async def _terminate_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        await process.wait()
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await asyncio.wait_for(process.wait(), timeout=5.0)


async def _exchange(
    request: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_PARSER_TIMEOUT_SECONDS,
    command: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not 0 < timeout_seconds <= 300:
        raise ValueError("Parser timeout must be in (0, 300] seconds")
    encoded = json_bytes(request)
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ParserIsolationError("Parser request exceeds IPC size limit")
    argv = command or (sys.executable, "-m", "app.ai.parser_child")
    async with _CHILD_SEMAPHORE:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(_backend_dir),
            env=_parser_environment(),
            close_fds=True,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                if process.stdin is None or process.stdout is None:
                    raise ParserIsolationError("Parser pipes are unavailable")
                process.stdin.write(struct.pack(">I", len(encoded)) + encoded)
                await process.stdin.drain()
                process.stdin.close()
                header = await process.stdout.readexactly(_FRAME_BYTES)
                length = struct.unpack(">I", header)[0]
                if length == 0 or length > MAX_RESPONSE_BYTES:
                    raise ParserIsolationError("Parser response exceeds IPC size limit")
                response = await process.stdout.readexactly(length)
                await process.wait()
                if process.returncode != 0:
                    raise ParserIsolationError("Parser child exited unsuccessfully")
                decoded = json.loads(response)
                if not isinstance(decoded, dict) or type(decoded.get("ok")) is not bool:
                    raise ParserIsolationError("Parser response is invalid")
                return decoded
        except TimeoutError as exc:
            raise ParserIsolationTimeout("Parser child timed out") from exc
        except (OSError, EOFError, asyncio.IncompleteReadError, ValueError) as exc:
            raise ParserIsolationError("Parser IPC failed") from exc
        finally:
            await _terminate_child(process)


def _dictionary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ParserIsolationError("Parser result shape is invalid")
    return value


def _entries(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise ParserIsolationError("Parser result shape is invalid")
    return value


def _date_or_none(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ParserIsolationError("Parser date is invalid")
    return date.fromisoformat(value)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ParserIsolationError("Parser number is invalid")
    result = Decimal(value)
    if not result.is_finite():
        raise ParserIsolationError("Parser number is invalid")
    return result


def _resume_from_payload(value: object) -> ParsedResumeResult:
    body = _dictionary(value)
    raw = body["raw_text"]
    if not isinstance(raw, str) or len(raw) > MAX_EXTRACTED_TEXT_CHARS:
        raise ParserIsolationError("Parser text is invalid")
    skills = tuple(
        ParsedSkill(
            **{
                **_dictionary(entry),
                "years_of_experience": _decimal_or_none(_dictionary(entry)["years_of_experience"]),
            }
        )
        for entry in _entries(body["skills"])
    )
    experiences = tuple(
        ParsedExperience(
            **{
                **_dictionary(entry),
                "start_date": _date_or_none(_dictionary(entry)["start_date"]),
                "end_date": _date_or_none(_dictionary(entry)["end_date"]),
            }
        )
        for entry in _entries(body["experiences"])
    )
    educations = tuple(
        ParsedEducation(
            **{
                **_dictionary(entry),
                "gpa": _decimal_or_none(_dictionary(entry)["gpa"]),
            }
        )
        for entry in _entries(body["educations"])
    )
    return ParsedResumeResult(
        raw_text=raw,
        profile=ParsedProfile(**_dictionary(body["profile"])),
        skills=skills,
        experiences=experiences,
        educations=educations,
    )


def _job_from_payload(value: object) -> ParsedJobResult:
    body = _dictionary(value)
    normalized = body["normalized_text"]
    if not isinstance(normalized, str) or len(normalized) > MAX_JOB_INPUT_CHARS:
        raise ParserIsolationError("Parser text is invalid")
    years = _decimal_or_none(body["min_experience_years"])
    if years is None:
        raise ParserIsolationError("Parser experience is invalid")
    skills = tuple(
        ParsedJobSkill(
            **{
                **_dictionary(entry),
                "min_years_required": _decimal_or_none(_dictionary(entry)["min_years_required"]),
            }
        )
        for entry in _entries(body["skills"])
    )
    return ParsedJobResult(
        normalized_text=normalized,
        min_experience_years=years,
        education_requirement=body["education_requirement"],
        skills=skills,
    )


def _taxonomy_payload(taxonomy: tuple[TaxonomySkill, ...]) -> list[dict[str, Any]]:
    if len(taxonomy) > MAX_TAXONOMY_ITEMS:
        raise ParserIsolationError("Taxonomy exceeds parser IPC limit")
    return [asdict(skill) for skill in taxonomy]


def _raise_child_error(response: dict[str, Any], kind: Literal["resume", "job"]) -> None:
    code = response.get("error_kind")
    if kind == "resume" and isinstance(code, str) and code in ResumeParseErrorKind:
        raise ResumeParseError(ResumeParseErrorKind(code), "Resume parsing failed")
    if kind == "job" and isinstance(code, str) and code in JobParseErrorKind:
        raise JobParseError(JobParseErrorKind(code), "Job parsing failed")
    raise ParserIsolationError("Parser child failed")


async def run_resume_parser_isolated(
    source_bytes: bytes, mime_type: str, taxonomy: tuple[TaxonomySkill, ...]
) -> ParsedResumeResult:
    if len(source_bytes) > MAX_RESUME_BYTES:
        raise ResumeParseError(ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED, "Resume file too large")
    request: dict[str, Any] = {
        "version": 1,
        "kind": "resume",
        "source_b64": base64.b64encode(source_bytes).decode("ascii"),
        "mime_type": mime_type,
        "taxonomy": _taxonomy_payload(taxonomy),
    }
    response = await _exchange(request)
    if not response["ok"]:
        _raise_child_error(response, "resume")
    try:
        return _resume_from_payload(response["result"])
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ParserIsolationError("Invalid resume parser result") from exc


async def run_job_parser_isolated(
    raw_content: str, taxonomy: tuple[TaxonomySkill, ...]
) -> ParsedJobResult:
    if not isinstance(raw_content, str):
        raise JobParseError(JobParseErrorKind.INVALID_INPUT, "Job content must be text")
    try:
        raw_content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise JobParseError(
            JobParseErrorKind.INVALID_INPUT, "Job content is invalid Unicode"
        ) from exc
    if len(raw_content) > MAX_JOB_INPUT_CHARS:
        raise JobParseError(JobParseErrorKind.TEXT_LIMIT_EXCEEDED, "Job text too large")
    request: dict[str, Any] = {
        "version": 1,
        "kind": "job",
        "raw_content": raw_content,
        "taxonomy": _taxonomy_payload(taxonomy),
    }
    response = await _exchange(request)
    if not response["ok"]:
        _raise_child_error(response, "job")
    try:
        return _job_from_payload(response["result"])
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ParserIsolationError("Invalid job parser result") from exc
