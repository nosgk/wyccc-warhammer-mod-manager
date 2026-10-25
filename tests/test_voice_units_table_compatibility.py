from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.api import API
from backend.models import ScanResult
from backend.game_data import (
    DbSource, GameDataEntry, _collect_effective_rows, _compare_internal_names,
    build_game_data_entries, parse_db_table,
)
from backend.game_data_patch_state import (
    build_game_data_inputs, ensure_game_data_patch, fingerprint_game_data_inputs,
)
from backend.start_options import (
    GAME_DATA_PATCH_NAME, PackEntry, collect_game_data_source_snapshot,
    read_pack_entries, voice_units_table_compatibility_requested, write_pfh5_pack,
)
from backend.storage import StateRepository
from tests.helpers import make_asset
from tests.test_game_data import _rows_for, _table_payload


def unit_entries(name: str, count: int, voice: str, version: int = 36):
    return (
        GameDataEntry(
            rf"db\main_units_tables\{name}",
            _table_payload("main_units_tables", version, [{
                "unit": "shared", "num_men": count, "land_unit": "shared_land",
                "upkeep_cost": count * 10, "audio_voiceover_culture": voice,
                "audio_voiceover_actor_group": voice, "has_spoken_vo": bool(voice),
                "vo_is_dragon": True,
            }]),
        ),
        GameDataEntry(
            rf"db\land_units_tables\{name}",
            _table_payload("land_units_tables", 54, [{
                "key": "shared_land", "melee_attack": count, "bonus_hit_points": count * 100,
                "selection_vo": voice, "selected_vo_secondary": voice,
                "selected_vo_tertiary": voice,
            }]),
        ),
    )


class VoiceUnitRowTests(unittest.TestCase):
    def test_both_unit_tables_keep_ordinary_fields_and_use_voice_fields(self):
        for voice_name, ordinary_name in [("!!!!!!!voice", "z_ordinary"), ("z_voice", "!!!!ordinary")]:
            for reverse in [False, True]:
                with self.subTest(voice_name=voice_name, reverse=reverse):
                    ordinary = DbSource("ordinary", unit_entries(ordinary_name, 60, "original"))
                    voice = DbSource("voice", unit_entries(voice_name, 10, "custom"), voice_units_table_compatibility=True)
                    sources = [voice, ordinary] if reverse else [ordinary, voice]
                    result = build_game_data_entries(sources, {})
                    self.assertEqual(result.stats["voice_units_table_rows_changed"], 2)
                    main = _rows_for(result, "main_units_tables")[0]
                    land = _rows_for(result, "land_units_tables")[0]
                    self.assertEqual(main["num_men"], 60)
                    self.assertEqual(main["upkeep_cost"], 600)
                    self.assertEqual(main["audio_voiceover_actor_group"], "custom")
                    self.assertEqual(land["melee_attack"], 60)
                    self.assertEqual(land["bonus_hit_points"], 6000)
                    for field in ["selection_vo", "selected_vo_secondary", "selected_vo_tertiary"]:
                        self.assertEqual(land[field], "custom")
                    for entry in result.entries:
                        name = entry.name.rsplit("\\", 1)[-1]
                        self.assertLess(_compare_internal_names(name, voice_name), 0)
                        self.assertLess(_compare_internal_names(name, ordinary_name), 0)

    def test_multiple_voice_mods_keep_internal_name_and_pack_order_priority(self):
        ordinary = DbSource("ordinary", unit_entries("a", 60, "original"))
        first = DbSource("first", unit_entries("same", 10, "first"), voice_units_table_compatibility=True)
        second = DbSource("second", unit_entries("same", 20, "second"), voice_units_table_compatibility=True)
        for voices in [[first, second], [second, first]]:
            result = build_game_data_entries([ordinary, *voices], {})
            values = _rows_for(result, "main_units_tables")[0]
            self.assertEqual(values["num_men"], 60)
            self.assertEqual(values["audio_voiceover_actor_group"], voices[0].name)

    def test_older_voice_table_does_not_clear_fields_missing_from_its_schema(self):
        ordinary = DbSource("ordinary", unit_entries("a", 60, "original"))
        voice = DbSource("voice", unit_entries("z", 10, "custom", version=33), voice_units_table_compatibility=True)
        effective = _collect_effective_rows([ordinary, voice], {"main_units_tables"})
        row = effective["main_units_tables"]["shared"].row
        self.assertTrue(row.values["vo_is_dragon"])
        self.assertEqual(row.values["audio_voiceover_actor_group"], "custom")

    def test_other_game_data_changes_apply_after_voice_merge(self):
        ordinary = DbSource("ordinary", unit_entries("z", 60, "original"))
        voice = DbSource("voice", unit_entries("!voice", 10, "custom"), voice_units_table_compatibility=True)
        result = build_game_data_entries([voice, ordinary], {"unit_model_multiplier": 2})
        self.assertEqual(_rows_for(result, "main_units_tables")[0]["num_men"], 120)
        self.assertEqual(_rows_for(result, "land_units_tables")[0]["melee_attack"], 60)

    def test_empty_voice_fields_are_values_not_fallback_requests(self):
        ordinary = DbSource("ordinary", unit_entries("a", 60, "original"))
        voice = DbSource("voice", unit_entries("z", 10, ""), voice_units_table_compatibility=True)
        result = build_game_data_entries([ordinary, voice], {})
        main = _rows_for(result, "main_units_tables")[0]
        land = _rows_for(result, "land_units_tables")[0]
        self.assertEqual(main["audio_voiceover_actor_group"], "")
        self.assertFalse(main["has_spoken_vo"])
        self.assertEqual(land["selection_vo"], "")
        self.assertEqual(main["num_men"], 60)

    def test_disabled_and_irrelevant_compatibility_produce_no_overlay(self):
        self.assertEqual(build_game_data_entries([DbSource("voice", unit_entries("voice", 10, "custom"))], {}).entries, ())
        self.assertEqual(build_game_data_entries([DbSource("voice", (), voice_units_table_compatibility=True)], {}).entries, ())
        voice = DbSource("voice", unit_entries("voice", 10, "custom"), voice_units_table_compatibility=True)
        self.assertEqual(build_game_data_entries([voice], {}).entries, ())


class VoiceUnitLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.output = self.root / "runtime"
        write_pfh5_pack(self.data / "db.pack", [PackEntry(e.name, e.payload) for e in unit_entries("data__", 30, "vanilla")])
        self.assets = {}
        for mod_id, name, count, voice in [("ordinary", "z", 60, "original"), ("voice", "!!!!voice", 10, "custom")]:
            path = self.root / f"{mod_id}.pack"
            write_pfh5_pack(path, [PackEntry(e.name, e.payload) for e in unit_entries(name, count, voice)])
            self.assets[mod_id] = make_asset(path, mod_id, "data")
        self.ids = ["voice", "ordinary"]

    def ensure(self):
        return ensure_game_data_patch(self.output, self.data, self.assets, self.ids, "default", {}, {}, game_id="warhammer3")

    def test_switch_alone_generates_reuses_and_removes_real_patch(self):
        before = self.ensure()
        self.assertEqual(before["status"], "zero_modification")
        self.assets["voice"].voice_units_table_compatibility = True
        generated = self.ensure()
        self.assertEqual(generated["status"], "generated")
        self.assertIn("voice_units_table_compatibility", generated["options"])
        self.assertEqual(generated["game_data"]["voice_units_table_rows_changed"], 2)
        self.assertNotEqual(before["fingerprint"], generated["fingerprint"])
        entries = read_pack_entries(Path(generated["path"]), ("db\\",))
        self.assertEqual(len(entries), 2)
        for entry in entries:
            table = entry.name.split("\\")[1]
            row = parse_db_table(table, entry.payload).rows[0].values
            self.assertEqual(row["num_men"] if table == "main_units_tables" else row["melee_attack"], 60)
        with patch("backend.game_data_patch_state.build_game_data_patch") as builder:
            self.assertEqual(self.ensure()["status"], "reused")
        builder.assert_not_called()
        self.assets["voice"].voice_units_table_compatibility = False
        self.assertEqual(self.ensure()["status"], "zero_modification")
        self.assertFalse((self.output / GAME_DATA_PATCH_NAME).exists())

    def test_snapshot_and_fingerprint_include_switch_with_all_other_settings_off(self):
        before = build_game_data_inputs(self.data, self.assets, self.ids, "default", {}, {})
        self.assets["voice"].voice_units_table_compatibility = True
        snapshot = collect_game_data_source_snapshot(self.data, self.assets, self.ids)
        self.assertTrue(snapshot.sources[0].voice_units_table_compatibility)
        self.assertTrue(snapshot.input_records("explicit")[0]["voice_units_table_compatibility"])
        after = build_game_data_inputs(self.data, self.assets, self.ids, "default", {}, {}, snapshot)
        self.assertNotEqual(fingerprint_game_data_inputs(before), fingerprint_game_data_inputs(after))

    def test_disabled_regular_mod_does_not_trigger_but_auto_movie_does(self):
        voice = self.assets["voice"]
        voice.voice_units_table_compatibility = True
        self.assertFalse(voice_units_table_compatibility_requested(self.data, self.assets, ["ordinary"]))
        movie = self.data / "auto_voice.pack"
        write_pfh5_pack(movie, [PackEntry(e.name, e.payload) for e in unit_entries("voice", 10, "movie")], pack_header_mask=4)
        asset = make_asset(movie, "movie", "data")
        asset.voice_units_table_compatibility = True
        self.assets[asset.id] = asset
        self.assertTrue(voice_units_table_compatibility_requested(self.data, self.assets, ["ordinary"]))
        self.ids = ["ordinary"]
        result = self.ensure()
        self.assertEqual(result["status"], "generated")
        self.assertIn(movie.name, result["source_diagnostics"]["auto_movie_pack_names"])

    def test_api_default_persistence_and_restore(self):
        api = API(self.root / "state")
        asset = self.assets["voice"]
        api._assets = self.assets
        self.assertFalse(asset.to_dict()["voice_units_table_compatibility"])
        result = api.call("set_voice_units_table_compatibility", [asset.id, True])
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["voice_units_table_compatibility"])
        repository = StateRepository(api.state_repository.database_path)
        self.assertTrue(repository.list_user_mod_data()[asset.id]["voice_units_table_compatibility"])
        game = self.root / "game"
        game.mkdir()
        (game / "data").mkdir()
        (game / "Warhammer3.exe").write_bytes(b"")
        (game / "data" / "manifest.txt").write_text("data.pack\t0\n", encoding="utf-8")
        self.assertTrue(api.call("save_settings", [{
            "game_path": str(game), "workshop_path": "", "fetch_workshop_metadata": False,
        }])["ok"])
        rescanned_asset = make_asset(Path(asset.path), asset.id, "data")
        with patch.object(api.scanner, "scan", return_value=ScanResult(mods=[rescanned_asset])):
            scanned = api.call("scan_mods", [False])
        self.assertTrue(scanned["ok"], scanned)
        self.assertTrue(scanned["data"]["mods"][0]["voice_units_table_compatibility"])
        self.assertTrue(api.call("set_voice_units_table_compatibility", [asset.id, False])["ok"])
        self.assertFalse(repository.list_user_mod_data()[asset.id]["voice_units_table_compatibility"])

    def test_old_database_migrates_switch_to_off(self):
        database = self.root / "legacy.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("CREATE TABLE user_mod_data (mod_id TEXT PRIMARY KEY, alias TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL); INSERT INTO user_mod_data VALUES('old', 'alias', 'notes', 1);")
        repository = StateRepository(database)
        self.assertFalse(repository.list_user_mod_data()["old"]["voice_units_table_compatibility"])
        self.assertTrue(repository.set_voice_units_table_compatibility("old", True))
        self.assertTrue(StateRepository(database).list_user_mod_data()["old"]["voice_units_table_compatibility"])
