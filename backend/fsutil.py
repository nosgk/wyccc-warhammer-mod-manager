"""Small filesystem helpers shared by backend services.

The project writes user state, caches, load-order files and generated Packs.
Every write that can be interrupted must go through a temporary file in the
destination directory and an atomic replace, so a crash never leaves a
half-written file behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_bytes(
    path: str | Path, data: bytes, *, mode: int | None = None, verify: bool = False
) -> Path:
    """Write bytes atomically and return the destination path.

    When ``verify`` is true, the destination is read back and compared. This is
    used by load-order writes, where a silent short write would make the game
    load the wrong MOD set.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
        if verify and target.read_bytes() != data:
            raise OSError(f"write verification failed: {target}")
    finally:
        temporary.unlink(missing_ok=True)
    return target


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
    verify: bool = False,
) -> Path:
    """Write text atomically and return the destination path."""
    return atomic_write_bytes(path, content.encode(encoding), mode=mode, verify=verify)


def atomic_copy(source: str | Path, target: str | Path) -> Path:
    """Copy a file atomically, preserving metadata."""
    source_path = Path(source)
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source_path, temporary)
        os.replace(temporary, target_path)
    finally:
        temporary.unlink(missing_ok=True)
    return target_path


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the lowercase SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` when the file does not exist."""
    source = Path(path)
    if not source.is_file():
        return default
    return json.loads(source.read_text(encoding="utf-8"))


def write_json(
    path: str | Path,
    payload: Any,
    *,
    indent: int = 2,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
) -> Path:
    """Serialize JSON atomically."""
    content = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys)
    return atomic_write_text(path, content)
