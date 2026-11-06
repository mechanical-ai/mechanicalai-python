"""What an upload is made of, and where a download lands.

An upload is given as a path, an open binary file, ``bytes`` (with a ``name``), or a ``(name, bytes_or_file)``
tuple. A path or a file backed by a real file descriptor is read from disk, never held whole in memory; an open file
is read from its current position. Up to ``MULTIPART_MAX_BYTES`` the bytes go in one multipart ``POST``; above
that, through a signed upload URL with the ``sha256:`` digest declared.

A download's file name is the artifact's or file's own name made safe for any file system: a bare name, no
control characters, no Windows-reserved stems, no trailing dots or spaces.
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Protocol

import anyio

__all__ = [
    "MULTIPART_MAX_BYTES",
    "FileInput",
    "UploadSource",
    "filename_for",
    "guess_content_type",
    "prepare_upload",
    "resolve_destination",
    "safe_filename",
]

MULTIPART_MAX_BYTES = 64 * 1024 * 1024
"""The multipart limit of ``POST /projects/{id}/files``; larger files use an upload URL."""

CHUNK_SIZE = 1024 * 1024

FileInput = str | os.PathLike[str] | bytes | bytearray | IO[bytes] | tuple[str, bytes | bytearray | IO[bytes]]
"""What ``files.upload`` accepts: a path, an open binary file, bytes with a ``name``, or a ``(name, bytes)`` tuple."""

_CONTENT_TYPES = {
    ".step": "model/step",
    ".stp": "model/step",
    ".stl": "model/stl",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".obj": "model/obj",
    ".iges": "model/iges",
    ".igs": "model/iges",
    ".dxf": "image/vnd.dxf",
    ".dwg": "image/vnd.dwg",
    ".vtu": "application/xml",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)


def guess_content_type(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


class ByteReader(Protocol):
    """What ``UploadSource.open`` hands out: a binary file open for one read pass."""

    def read(self, n: int = -1, /) -> bytes: ...

    def close(self) -> None: ...

    def __enter__(self) -> ByteReader: ...

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None, /
    ) -> None: ...


class _KeepOpen:
    """A caller's file, handed out for one read pass; closing this does not close theirs."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream

    def read(self, n: int = -1, /) -> bytes:
        return self._stream.read(n)

    def close(self) -> None:
        return None

    def __enter__(self) -> _KeepOpen:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None, /
    ) -> None:
        return None


class UploadSource:
    """One file ready to send: its name, size and content type, and a way to read its bytes again and again."""

    def __init__(
        self,
        *,
        name: str,
        size: int,
        content_type: str,
        path: Path | None = None,
        data: bytes | None = None,
        stream: IO[bytes] | None = None,
        start: int = 0,
    ) -> None:
        self.name = name
        self.size = size
        self.content_type = content_type
        self._path = path
        self._data = data
        self._stream = stream
        self._start = start
        self._digest: str | None = None

    def open(self) -> ByteReader:
        """A fresh read pass over the bytes (from the start); the caller closes it."""
        if self._path is not None:
            return open(self._path, "rb")
        if self._stream is not None:
            self._stream.seek(self._start)
            return _KeepOpen(self._stream)
        return io.BytesIO(self._data or b"")

    def read_bytes(self) -> bytes:
        """The whole content (for the multipart body, at most ``MULTIPART_MAX_BYTES``)."""
        with self.open() as stream:
            return stream.read()

    def iter_bytes(self, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        with self.open() as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    return
                yield chunk

    async def aiter_bytes(self, chunk_size: int = CHUNK_SIZE) -> AsyncIterator[bytes]:
        """The bytes without blocking the event loop: a path through ``anyio.open_file``, a file in a thread."""
        if self._path is not None:
            async with await anyio.open_file(self._path, "rb") as stream:
                while True:
                    chunk = await stream.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
            return
        reader = await anyio.to_thread.run_sync(self.open)
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(reader.read, chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            reader.close()

    def digest(self) -> str:
        """``sha256:<hex>`` of the bytes, computed on first use by streaming them."""
        if self._digest is None:
            digest = hashlib.sha256()
            for chunk in self.iter_bytes():
                digest.update(chunk)
            self._digest = f"sha256:{digest.hexdigest()}"
        return self._digest

    async def adigest(self) -> str:
        return await anyio.to_thread.run_sync(self.digest)

    @property
    def uses_multipart(self) -> bool:
        return self.size <= MULTIPART_MAX_BYTES


def _fileno(stream: Any) -> int | None:
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    return fileno if isinstance(fileno, int) and fileno >= 0 else None


def prepare_upload(file: FileInput, *, name: str | None = None, content_type: str | None = None) -> UploadSource:
    """Turn any accepted input into an ``UploadSource`` with its name, size and content type known."""
    inferred_name: str | None = None
    path: Path | None = None
    data: bytes | None = None
    stream: IO[bytes] | None = None
    start = 0
    size: int

    if isinstance(file, tuple):
        inferred_name, payload = file
        file = payload
    if isinstance(file, (str, os.PathLike)):
        path = Path(file)
        if not path.is_file():
            raise FileNotFoundError(f"No such file: {path}")
        inferred_name = inferred_name or path.name
        size = path.stat().st_size
    elif isinstance(file, (bytes, bytearray)):
        data = bytes(file)
        size = len(data)
    elif hasattr(file, "read"):
        stream_name: Any = getattr(file, "name", None)
        if inferred_name is None and isinstance(stream_name, (str, os.PathLike)):
            inferred_name = Path(stream_name).name
        fileno = _fileno(file)
        seekable = bool(getattr(file, "seekable", lambda: False)())
        if fileno is not None and seekable:
            start = file.tell()
            size = max(0, os.fstat(fileno).st_size - start)
            stream = file
        else:
            read = file.read()
            if isinstance(read, str):
                raise TypeError("Open the file in binary mode ('rb').")
            data = bytes(read)
            size = len(data)
    else:
        raise TypeError(
            "A file is a path, an open binary file, bytes with a name, or a (name, bytes) tuple; "
            f"got {type(file).__name__}."
        )

    final_name = name or inferred_name
    if not final_name:
        raise ValueError("Give the file a name: files.upload(project_id, data, name='part.step').")
    final_name = Path(final_name).name

    return UploadSource(
        name=final_name,
        size=size,
        content_type=content_type or guess_content_type(final_name),
        path=path,
        data=data,
        stream=stream,
        start=start,
    )


def safe_filename(name: str, *, fallback: str = "download") -> str:
    """A bare file name any file system takes: no directories, control or reserved characters, reserved stems,
    trailing dots or spaces."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = _CONTROL_CHARS.sub("", base)
    base = _ILLEGAL_CHARS.sub("_", base)
    base = base.strip().rstrip(". ")
    if base.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        base = f"_{base}"
    if base in ("", ".", ".."):
        return fallback
    return base


def filename_for(name: str, fmt: str | None) -> str:
    """``<name>.<format>`` unless the name already ends with that extension; always a safe bare name."""
    base = safe_filename(name)
    if fmt and not base.lower().endswith(f".{fmt.lower()}"):
        return f"{base}.{safe_filename(fmt, fallback='bin')}"
    return base


def resolve_destination(dest: str | os.PathLike[str], default_name: str) -> Path:
    """Where to write: ``dest`` is a directory (created if needed) that takes ``default_name``, or a file path.

    A directory is one that exists as such, ends with a separator, or has no file extension (``./out``); anything
    else (``parts/bracket-v2.stl``) is the file to write.
    """
    raw = os.fspath(dest)
    target = Path(raw)
    if target.is_dir() or raw.endswith(("/", os.sep)) or (not target.suffix and not target.is_file()):
        target.mkdir(parents=True, exist_ok=True)
        return target / default_name
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
