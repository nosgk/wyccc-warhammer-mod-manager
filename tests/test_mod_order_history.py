from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.mod_order_history import ModOrderHistoryStore


def _record(record_id: str, *, total: int = 3) -> dict:
    return {
        "id": record_id,
        "created_at": 1,
        "completed_at": 2,
        "game_id": "warhammer3",
        "total_mods": total,
        "ai_enabled": True,
        "degraded": False,
        "model": "example-model",
        "baseline": "current",
        "recommended_order": ["a", "b", "c"],
        "result": {"recommended_order": ["a", "b", "c"]},
    }


class ModOrderHistoryTests(unittest.TestCase):
    def test_persists_and_lists_summaries_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.json"
            store = ModOrderHistoryStore(path)
            store.add(_record("one"))
            store.add(_record("two"))
            self.assertEqual([item["id"] for item in store.list()], ["two", "one"])
            self.assertEqual(store.get("one")["recommended_order"], ["a", "b", "c"])
            self.assertTrue(path.is_file())

    def test_mark_applied_updates_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ModOrderHistoryStore(Path(temporary) / "history.json")
            store.add(_record("one"))
            updated = store.mark_applied("one", ["c", "b", "a"])
            self.assertEqual(updated["applied_order"], ["c", "b", "a"])
            self.assertGreater(updated["applied_at"], 0)

    def test_replacing_a_record_does_not_duplicate_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ModOrderHistoryStore(Path(temporary) / "history.json")
            store.add(_record("one"))
            store.add(_record("one", total=9))
            self.assertEqual(len(store.list()), 1)
            self.assertEqual(store.get("one")["total_mods"], 9)

    def test_trims_to_max_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ModOrderHistoryStore(Path(temporary) / "history.json", max_records=2)
            for index in range(4):
                store.add(_record(f"record-{index}"))
            self.assertEqual([item["id"] for item in store.list()], ["record-3", "record-2"])

    def test_missing_record_raises(self) -> None:
        store = ModOrderHistoryStore(None)
        with self.assertRaisesRegex(ValueError, "historyNotFound"):
            store.get("missing")


if __name__ == "__main__":
    unittest.main()
