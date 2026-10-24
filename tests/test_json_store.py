from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.json_store import AtomicJsonStore


class AtomicJsonStoreTests(unittest.TestCase):
    def test_missing_file_returns_a_fresh_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AtomicJsonStore(Path(temporary) / "value.json", lambda: {"items": []})
            first = store.load()
            first["items"].append("x")
            self.assertEqual(store.load(), {"items": []})

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "value.json"
            store = AtomicJsonStore(path, dict)
            store.save({"b": 2, "a": 1})
            self.assertEqual(store.load(), {"a": 1, "b": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1, "b": 2})

    def test_update_merges_with_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AtomicJsonStore(Path(temporary) / "value.json", dict)
            store.save({"a": 1})
            self.assertEqual(store.update({"b": 2}), {"a": 1, "b": 2})

    def test_corrupt_file_is_quarantined_and_default_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "value.json"
            path.write_text("{not json", encoding="utf-8")
            store = AtomicJsonStore(path, lambda: {"ok": True})
            self.assertEqual(store.load(), {"ok": True})
            self.assertFalse(path.exists())
            self.assertEqual(len(list(root.glob("value.json.corrupt-*"))), 1)

    def test_non_object_root_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "value.json"
            path.write_text("[]", encoding="utf-8")
            store = AtomicJsonStore(path, dict)
            self.assertEqual(store.load(), {})
            self.assertEqual(len(list(root.glob("value.json.corrupt-*"))), 1)


if __name__ == "__main__":
    unittest.main()
