from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol


@dataclass(frozen=True)
class PutIfAbsentResult:
    created: bool
    fingerprint: str


class ResumeStorage(Protocol):
    async def put_if_absent(self, key: str, data: bytes) -> PutIfAbsentResult: ...

    async def read_bytes(self, key: str) -> bytes: ...


class LocalResumeStorage:
    """Local storage with atomic, no-overwrite publication by logical object key."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for_key(self, key: str) -> Path:
        logical = PurePosixPath(key)
        if (
            logical.is_absolute()
            or not logical.parts
            or any(part in {"", ".", ".."} for part in logical.parts)
        ):
            raise ValueError("Invalid storage key")

        candidate = self._root.joinpath(*logical.parts)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = candidate.parent.resolve()
        if not resolved_parent.is_relative_to(self._root):
            raise ValueError("Storage key escapes configured root")
        target = resolved_parent / candidate.name
        if target.is_symlink():
            raise ValueError("Symbolic-link storage objects are not supported")
        return target

    def _fingerprint_file(self, path: Path) -> str:
        if path.is_symlink() or not path.resolve().is_relative_to(self._root):
            raise ValueError("Stored object escapes configured root")
        digest = hashlib.sha256()
        with path.open("rb") as stored:
            while chunk := stored.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _put_if_absent(self, key: str, data: bytes) -> PutIfAbsentResult:
        target = self._path_for_key(key)
        request_fingerprint = hashlib.sha256(data).hexdigest()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                return PutIfAbsentResult(
                    created=False,
                    fingerprint=self._fingerprint_file(target),
                )
            return PutIfAbsentResult(created=True, fingerprint=request_fingerprint)
        finally:
            temporary.unlink(missing_ok=True)

    async def put_if_absent(self, key: str, data: bytes) -> PutIfAbsentResult:
        return await asyncio.to_thread(self._put_if_absent, key, data)

    async def read_bytes(self, key: str) -> bytes:
        target = self._path_for_key(key)
        return await asyncio.to_thread(target.read_bytes)
