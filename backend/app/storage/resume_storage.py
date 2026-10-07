from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol


@dataclass(frozen=True)
class PutIfAbsentResult:
    created: bool
    fingerprint: str


class ResumeStorage(Protocol):
    async def put_if_absent(self, key: str, data: bytes) -> PutIfAbsentResult: ...

    async def read_bytes(self, key: str) -> bytes: ...

    async def read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes: ...


class LocalResumeStorage:
    """Local storage with atomic, no-overwrite publication by logical object key."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _relative_parts(self, key: str) -> tuple[str, ...]:
        logical = PurePosixPath(key)
        if (
            "\x00" in key
            or "\\" in key
            or logical.is_absolute()
            or PureWindowsPath(key).is_absolute()
            or bool(PureWindowsPath(key).drive)
            or not logical.parts
            or any(part in {"", ".", ".."} for part in logical.parts)
        ):
            raise ValueError("Invalid storage key")
        return logical.parts

    def _path_for_write(self, key: str) -> Path:
        parts = self._relative_parts(key)
        unresolved_parent = self._root.joinpath(*parts[:-1])
        current = self._root
        for part in parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError("Storage key escapes configured root")

        resolved_parent = unresolved_parent.resolve(strict=False)
        if not resolved_parent.is_relative_to(self._root):
            raise ValueError("Storage key escapes configured root")
        resolved_parent.mkdir(parents=True, exist_ok=True)
        target = resolved_parent / parts[-1]
        if target.is_symlink():
            raise ValueError("Symbolic-link storage objects are not supported")
        return target

    def _path_for_read(self, key: str) -> Path:
        parts = self._relative_parts(key)
        current = self._root
        for part in parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Symbolic-link storage paths are not supported")
        if not current.resolve(strict=False).is_relative_to(self._root):
            raise ValueError("Storage key escapes configured root")
        if not current.exists():
            raise FileNotFoundError("Stored object does not exist")
        if not current.is_file():
            raise ValueError("Stored object is not a regular file")
        return current

    def _fingerprint_file(self, path: Path) -> str:
        if path.is_symlink() or not path.resolve().is_relative_to(self._root):
            raise ValueError("Stored object escapes configured root")
        digest = hashlib.sha256()
        with path.open("rb") as stored:
            while chunk := stored.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _put_if_absent(self, key: str, data: bytes) -> PutIfAbsentResult:
        target = self._path_for_write(key)
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
        target = self._path_for_read(key)
        return await asyncio.to_thread(target.read_bytes)

    def _read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes:
        target = self._path_for_read(key)
        read_limit = min(expected_size, maximum_size) + 1
        with target.open("rb") as source:
            return source.read(read_limit)

    async def read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes:
        return await asyncio.to_thread(
            self._read_bytes_bounded,
            key,
            expected_size=expected_size,
            maximum_size=maximum_size,
        )
