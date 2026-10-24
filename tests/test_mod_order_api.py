from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.api import API
from tests.helpers import write_pack


def _wait_for(api: API, session_id: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = api.call("get_mod_order_analysis", [session_id])
        if not response["ok"]:
            raise AssertionError(response)
        state = response["data"]
        if not state["running"]:
            return state
        time.sleep(0.01)
    raise AssertionError("order analysis did not finish")


class ModOrderApiTests(unittest.TestCase):
    def test_analysis_and_apply_update_the_load_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "Total War WARHAMMER III"
            data = game / "data"
            data.mkdir(parents=True)
            (game / "Warhammer3.exe").write_bytes(b"")
            (data / "manifest.txt").write_text("data.pack\t0\ndb.pack\t0\n", encoding="utf-8")
            write_pack(data / "data.pack")
            write_pack(data / "db.pack")
            write_pack(data / "b.pack")
            write_pack(data / "a.pack", dependencies=["b.pack"])

            api = API(root / "state")
            saved = api.call(
                "save_settings",
                [{"game_path": str(game), "workshop_path": "", "fetch_workshop_metadata": False}],
            )
            self.assertTrue(saved["ok"], saved)
            scan = api.call("scan_mods", [False])
            self.assertTrue(scan["ok"], scan)
            by_pack = {mod["pack_name"]: mod["id"] for mod in scan["data"]["mods"]}
            a_id = by_pack["a.pack"]
            b_id = by_pack["b.pack"]

            started = api.call("start_mod_order_analysis", [[a_id, b_id]])
            self.assertTrue(started["ok"], started)
            state = _wait_for(api, started["data"]["id"])
            self.assertEqual(state["status"], "ready")
            self.assertEqual(state["result"]["recommended_order"], [b_id, a_id])

            applied = api.call("apply_mod_order_analysis", [started["data"]["id"]])
            self.assertTrue(applied["ok"], applied)
            self.assertEqual(applied["data"]["ordered_mod_ids"], [b_id, a_id])
            content = (game / "used_mods.txt").read_text(encoding="utf-8")
            self.assertLess(content.index('"b.pack"'), content.index('"a.pack"'))


if __name__ == "__main__":
    unittest.main()
