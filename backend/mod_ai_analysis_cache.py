from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any


MAX_CACHE_BYTES = 100 * 1024 * 1024
CACHE_VERSION = 1


def analysis_cache_key(kind: str, settings: dict[str, Any], evidence: Any) -> str:
    """Key the model output by its input and generation rules, without storing credentials."""
    payload = {
        "version": CACHE_VERSION, "kind": kind, "evidence": evidence,
        "configuration": {
            "model": str(settings.get("ai_model") or "").strip(),
            "base_url": str(settings.get("ai_base_url") or "").strip().rstrip("/"),
            "language": str(settings.get("language") or "en-US"),
            "temperature": float(settings.get("ai_temperature", 0.3)),
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ModAIAnalysisCache:
    """Rebuildable, atomic disk entries with a persistent least-recently-used size limit."""

    def __init__(self, directory: Path | None, max_bytes: int = MAX_CACHE_BYTES) -> None:
        self.directory = Path(directory) if directory is not None else None
        self.max_bytes = max(0, max_bytes)
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, int] = OrderedDict()
        self._bytes = 0
        self._loaded = False

    @staticmethod
    def _valid_key(key: str) -> bool:
        return len(key) == 64 and all(character in "0123456789abcdef" for character in key)

    def _load(self) -> None:
        if self._loaded or self.directory is None:
            return
        self._loaded = True
        records = []
        try:
            for path in self.directory.iterdir():
                # Only remove this cache's interrupted writes, never arbitrary neighboring files.
                if path.name.startswith(".") and path.suffix == ".tmp" and self._valid_key(path.name.split(".")[1]):
                    path.unlink(missing_ok=True)
                elif path.suffix == ".json" and self._valid_key(path.stem) and not path.is_symlink():
                    stat = path.stat()
                    if path.is_file():
                        records.append((stat.st_mtime_ns, path.stem, stat.st_size))
        except OSError:
            pass
        for _accessed_at, key, size in sorted(records):
            self._entries[key] = size
            self._bytes += size
        self._prune(self.max_bytes)

    def _remove(self, key: str) -> bool:
        if self.directory is None:
            return False
        try:
            (self.directory / f"{key}.json").unlink(missing_ok=True)
        except OSError:
            return False
        self._bytes -= self._entries.pop(key, 0)
        return True

    def _prune(self, target_bytes: int) -> None:
        if self._bytes <= target_bytes:
            return
        for key in list(self._entries):
            if self._bytes <= target_bytes:
                break
            self._remove(key)

    @property
    def size_bytes(self) -> int:
        with self._lock:
            self._load()
            return self._bytes

    def get(self, key: str) -> Any | None:
        if self.directory is None or not self._valid_key(key):
            return None
        with self._lock:
            self._load()
            if key not in self._entries:
                return None
            path = self.directory / f"{key}.json"
            try:
                if path.is_symlink() or path.stat().st_size > self.max_bytes:
                    raise ValueError("Unexpected cache file")
                data = path.read_bytes()
                if len(data) > self.max_bytes:
                    raise ValueError("Oversized cache entry")
                payload = json.loads(data.decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
                    raise ValueError("Invalid cache entry")
                os.utime(path, None)
                self._bytes += len(data) - self._entries[key]
                self._entries[key] = len(data)
                self._entries.move_to_end(key)
                self._prune(self.max_bytes)
                return payload.get("value")
            except (OSError, ValueError):
                self._remove(key)
                return None

    def put(self, key: str, value: Any) -> bool:
        if self.directory is None or not self._valid_key(key):
            return False
        data = json.dumps({"version": CACHE_VERSION, "value": value}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > self.max_bytes:
            return False
        with self._lock:
            self._load()
            temporary = self.directory / f".{key}.{uuid.uuid4().hex}.tmp"
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                if key in self._entries and not self._remove(key):
                    return False
                self._prune(self.max_bytes - len(data))
                if self._bytes + len(data) > self.max_bytes:
                    return False
                with temporary.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.directory / f"{key}.json")
                self._entries[key] = len(data)
                self._bytes += len(data)
                return True
            except OSError:
                return False
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
