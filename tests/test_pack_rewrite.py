from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.pack_reader import read_pack_index
from backend.pack_rewrite import pack_digest, replace_pack_entries
from tests.helpers import write_pack


class PackRewriteTests(unittest.TestCase):
    def _pack(self, root: Path) -> Path:
        return write_pack(
            root / "mod.pack",
            entries=[("a.txt", b"old-a"), ("b.txt", b"old-b")],
        )

    def test_replaces_selected_entries_and_backs_up_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self._pack(root)
            original = pack_digest(pack)
            backup = replace_pack_entries(
                pack,
                {"a.txt": b"new-a"},
                root / "backups",
                original,
            )
            self.assertTrue(backup.is_file())
            self.assertEqual(pack_digest(backup), original)
            entries = read_pack_index(pack)
            self.assertEqual([entry.name for entry in entries], ["a.txt", "b.txt"])
            self.assertEqual(pack.read_bytes().count(b"new-a"), 1)
            self.assertEqual(pack.read_bytes().count(b"old-b"), 1)

    def test_rejects_a_changed_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self._pack(root)
            with self.assertRaisesRegex(ValueError, "schemaUpdate.fileChanged"):
                replace_pack_entries(
                    pack,
                    {"a.txt": b"new-a"},
                    root / "backups",
                    "0" * 64,
                )
            self.assertEqual(pack.read_bytes().count(b"old-a"), 1)

    def test_rejects_unknown_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self._pack(root)
            with self.assertRaisesRegex(ValueError, "schemaUpdate.invalidPack"):
                replace_pack_entries(
                    pack,
                    {"missing.txt": b"x"},
                    root / "backups",
                    pack_digest(pack),
                )

    def test_digest_matches_hashlib(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = self._pack(Path(temporary))
            expected = hashlib.sha256(pack.read_bytes()).hexdigest()
            self.assertEqual(pack_digest(pack), expected)


if __name__ == "__main__":
    unittest.main()
