from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import API
from backend.scanner import _asset_id
from tests.helpers import write_pack


class WorkshopUpdateEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        game = root / "game"
        self.data = game / "data"
        self.data.mkdir(parents=True)
        (game / "Warhammer3.exe").write_bytes(b"")
        self.workshop = root / "workshop"
        self.api = API(root / "state")
        # SQLite may leave WAL files briefly after the last connection closes.
        self.addCleanup(shutil.rmtree, root / "state", True)
        self.addCleanup(self.api.close)
        self.api.settings_service.save({
            "game_path": str(game),
            "workshop_path": str(self.workshop),
            "check_outdated_mods": False,
            "fetch_workshop_metadata": False,
            "live_mod_detection": False,
        })
        current_user = patch("backend.api.get_current_user", return_value={"steam_id": "765"})
        self.current_user = current_user.start()
        self.addCleanup(current_user.stop)

    def cache_owner(self, owner: str, *, app_id: int = 1142710) -> None:
        self.api.workshop_service.store.save({
            "items": {"123": {"creator_id": owner, "app_id": app_id}},
            "authors": {},
        })

    def scan_and_check_eligibility(self, *, refresh: bool = False, eligible: bool = True) -> list[dict]:
        scanned = self.api.call("scan_mods", [refresh])
        self.assertTrue(scanned["ok"], scanned)
        mods = scanned["data"]["mods"]
        ids = [mod["id"] for mod in mods]
        result = self.api.call("get_workshop_update_eligibility", [ids])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["eligible_mod_ids"], ids if eligible else [])
        return mods

    def test_owned_mod_keeps_update_eligibility_when_data_copy_is_added_and_removed(self) -> None:
        write_pack(self.workshop / "123" / "own.pack")
        self.cache_owner("765")
        for present in (False, True, False, True):
            with self.subTest(data_present=present):
                local = self.data / "own.pack"
                if present:
                    write_pack(local)
                else:
                    local.unlink(missing_ok=True)
                mods = self.scan_and_check_eligibility()
                self.assertEqual(len(mods), 1)
                self.assertEqual(mods[0]["source"], "data" if present else "workshop")

    def test_linked_data_mod_restores_owner_with_or_without_matching_subscription_filename(self) -> None:
        local = write_pack(self.data / "local.pack")
        mod_id = _asset_id("data", local)
        self.api.state_repository.set_published_workshop_id(mod_id, "123")
        self.cache_owner("765")
        for subscribed in (False, True):
            with self.subTest(subscribed=subscribed):
                if subscribed:
                    write_pack(self.workshop / "123" / "published.pack")
                mods = self.scan_and_check_eligibility()
                local_mod = next(mod for mod in mods if mod["id"] == mod_id)
                self.assertEqual(local_mod["creator_id"], "765")
                self.assertEqual(local_mod["path"], str(local.resolve()))

    def test_refresh_recovers_linked_data_mod_ownership(self) -> None:
        local = write_pack(self.data / "local.pack")
        self.api.state_repository.set_published_workshop_id(_asset_id("data", local), "123")
        self.scan_and_check_eligibility(eligible=False)

        def refresh(ids, language, *, app_id, steamworks_first):
            self.assertEqual(ids, ["123"])
            self.assertEqual(app_id, 1142710)
            self.cache_owner("765")

        with patch.object(self.api.workshop_service, "refresh", side_effect=refresh) as remote:
            self.scan_and_check_eligibility(refresh=True)
        remote.assert_called_once()

    def test_linked_data_mod_keeps_update_eligibility_after_restart(self) -> None:
        local = write_pack(self.data / "local.pack")
        self.api.state_repository.set_published_workshop_id(_asset_id("data", local), "123")
        self.cache_owner("765")
        self.scan_and_check_eligibility()

        state_dir = self.api.data_dir
        self.api.close()
        self.api = API(state_dir)
        self.addCleanup(self.api.close)
        self.scan_and_check_eligibility()

    def test_published_link_does_not_grant_ownership_without_matching_current_account(self) -> None:
        local = write_pack(self.data / "local.pack")
        self.api.state_repository.set_published_workshop_id(_asset_id("data", local), "123")
        for owner, current, app_id in (
            ("", "765", 1142710),
            ("999", "765", 1142710),
            ("765", "999", 1142710),
            ("765", "", 1142710),
            ("765", "765", 779340),
        ):
            with self.subTest(owner=owner, current=current, app_id=app_id):
                self.cache_owner(owner, app_id=app_id)
                self.current_user.return_value = {"steam_id": current}
                self.scan_and_check_eligibility(eligible=False)


if __name__ == "__main__":
    unittest.main()
