from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.constants import SOURCE_DATA, SOURCE_WORKSHOP
from backend.file_operations import _validated_pack_path, workshop_item_directory
from backend.models import GamePaths


class PackPathValidationTests(unittest.TestCase):
    def test_accepts_pack_at_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            pack = data / "mod.pack"
            pack.write_bytes(b"pack")
            paths = GamePaths(game_path=str(Path(temporary)), data_path=str(data))
            self.assertEqual(_validated_pack_path(pack, SOURCE_DATA, paths), pack.resolve())

    def test_rejects_pack_below_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            nested = data / "sub"
            nested.mkdir(parents=True)
            pack = nested / "mod.pack"
            pack.write_bytes(b"pack")
            paths = GamePaths(game_path=str(Path(temporary)), data_path=str(data))
            with self.assertRaisesRegex(ValueError, "Data 根目录"):
                _validated_pack_path(pack, SOURCE_DATA, paths)

    def test_rejects_non_pack_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            path = data / "mod.txt"
            path.write_text("x", encoding="utf-8")
            paths = GamePaths(game_path=str(Path(temporary)), data_path=str(data))
            with self.assertRaisesRegex(ValueError, "Pack 文件"):
                _validated_pack_path(path, SOURCE_DATA, paths)

    def test_accepts_workshop_item_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workshop = Path(temporary) / "workshop"
            item = workshop / "123"
            item.mkdir(parents=True)
            pack = item / "mod.pack"
            pack.write_bytes(b"pack")
            paths = GamePaths(game_path=str(Path(temporary)), workshop_path=str(workshop))
            self.assertEqual(_validated_pack_path(pack, SOURCE_WORKSHOP, paths), pack.resolve())

    def test_rejects_workshop_pack_without_numeric_item_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workshop = Path(temporary) / "workshop"
            item = workshop / "not-a-number"
            item.mkdir(parents=True)
            pack = item / "mod.pack"
            pack.write_bytes(b"pack")
            paths = GamePaths(game_path=str(Path(temporary)), workshop_path=str(workshop))
            with self.assertRaisesRegex(ValueError, "Workshop 项目目录"):
                _validated_pack_path(pack, SOURCE_WORKSHOP, paths)

    def test_rejects_workshop_nested_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workshop = Path(temporary) / "workshop"
            nested = workshop / "123" / "sub"
            nested.mkdir(parents=True)
            pack = nested / "mod.pack"
            pack.write_bytes(b"pack")
            paths = GamePaths(game_path=str(Path(temporary)), workshop_path=str(workshop))
            with self.assertRaisesRegex(ValueError, "Workshop 项目目录"):
                _validated_pack_path(pack, SOURCE_WORKSHOP, paths)

    def test_rejects_workshop_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workshop = Path(temporary) / "workshop"
            (workshop / "123").mkdir(parents=True)
            outside = Path(temporary) / "outside.pack"
            outside.write_bytes(b"pack")
            paths = GamePaths(game_path=str(Path(temporary)), workshop_path=str(workshop))
            with self.assertRaisesRegex(ValueError, "Workshop 目录"):
                _validated_pack_path(workshop / "123" / ".." / ".." / "outside.pack", SOURCE_WORKSHOP, paths)

    def test_rejects_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = GamePaths(game_path=str(Path(temporary)))
            with self.assertRaisesRegex(ValueError, "不支持删除来源"):
                _validated_pack_path(Path(temporary) / "mod.pack", "other", paths)


class WorkshopDirectoryTests(unittest.TestCase):
    def test_accepts_numeric_item_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(workshop_item_directory(root, "123"), (root / "123").resolve())

    def test_rejects_non_numeric_or_traversal_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for value in ("abc", "..", "../123", "123/456", ""):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, "Workshop ID"):
                        workshop_item_directory(root, value)


if __name__ == "__main__":
    unittest.main()
