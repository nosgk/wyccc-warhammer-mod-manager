from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.fsutil import (
    atomic_copy,
    atomic_write_bytes,
    atomic_write_text,
    read_json,
    sha256_file,
    write_json,
)


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_text_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "value.txt"
            atomic_write_text(path, "first")
            atomic_write_text(path, "second")
            self.assertEqual(path.read_text(encoding="utf-8"), "second")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_atomic_write_bytes_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "a" / "b" / "value.bin"
            atomic_write_bytes(path, b"\x00\x01")
            self.assertEqual(path.read_bytes(), b"\x00\x01")

    def test_atomic_copy_preserves_content_and_leaves_no_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pack"
            target = root / "target" / "target.pack"
            source.write_bytes(b"payload")
            atomic_copy(source, target)
            self.assertEqual(target.read_bytes(), b"payload")
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_failed_replace_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "value.txt"
            original_replace = os.replace

            def fail_replace(source, target):
                raise OSError("replace failed")

            os.replace = fail_replace  # type: ignore[assignment]
            try:
                with self.assertRaises(OSError):
                    atomic_write_text(path, "content")
            finally:
                os.replace = original_replace  # type: ignore[assignment]
            self.assertFalse(path.exists())
            self.assertEqual(list(root.glob("*.tmp")), [])


class JsonTests(unittest.TestCase):
    def test_read_json_returns_default_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(read_json(Path(temporary) / "missing.json", {"a": 1}), {"a": 1})

    def test_write_json_is_sorted_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "value.json"
            write_json(path, {"b": 2, "a": 1})
            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "a": 1,\n  "b": 2\n}')
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1, "b": 2})


class HashTests(unittest.TestCase):
    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "value.bin"
            path.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )


if __name__ == "__main__":
    unittest.main()
