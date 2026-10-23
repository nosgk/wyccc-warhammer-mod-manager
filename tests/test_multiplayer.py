from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.api import API
from backend.app_settings import SettingsService
from backend.constants import INTERNAL_FEATURE_PACK_NAMES, RUNTIME_OPTIONS_MARKER_ENTRY
from backend.multiplayer import DISABLED_PACK_SUFFIX, restore_launcher_packs
from backend.start_options import RUNTIME_PACK_NAME, UNIT_DATA_PATCH_NAME
from tests.helpers import write_pack
from tests.test_last_launch_comparison import select_game


@pytest.fixture
def manager(tmp_path):
    api = API(tmp_path / "state")
    with patch.object(api, "detect_game_running", return_value=False):
        yield api
    api.close()


def scan(api):
    response = api.call("scan_mods", [False])
    assert response["ok"], response
    return response["data"]


def test_switch_is_persistent_and_keeps_scanned_mods_and_patch_settings(manager, tmp_path):
    game = select_game(manager, tmp_path)
    write_pack(game / "data" / "user.pack")
    scan(manager)
    before = dict(manager._assets)
    assert manager.settings_service.get()["multiplayer_mode"] is False
    for enabled in (True, False):
        result = manager.call("set_multiplayer_mode", [enabled])
        assert result["ok"], result
        assert result["data"]["settings"]["multiplayer_mode"] is enabled
        assert SettingsService(manager.data_dir).get()["multiplayer_mode"] is enabled
        assert manager._assets == before
        assert result["data"]["settings"]["variant_selector_compatibility_patch_enabled"] is True


@pytest.mark.parametrize("enabled", [True, False])
def test_switch_is_locked_while_game_is_running(manager, tmp_path, enabled):
    select_game(manager, tmp_path)
    with patch.object(manager, "detect_game_running", return_value=True):
        result = manager.call("set_multiplayer_mode", [enabled])
    assert not result["ok"]
    assert manager.settings_service.get()["multiplayer_mode"] is False


def test_multiplayer_loads_only_enabled_mods_and_suspends_all_launcher_packs(manager, tmp_path):
    game = select_game(manager, tmp_path)
    data = game / "data"
    user = write_pack(data / "user.pack")
    disabled = write_pack(data / "disabled.pack")
    managed = [write_pack(data / name, byte_mask=4) for name in INTERNAL_FEATURE_PACK_NAMES]
    managed.append(write_pack(data / "renamed_runtime.pack", byte_mask=4,
                              entries=[(RUNTIME_OPTIONS_MARKER_ENTRY, b"{}")]))
    original = {path: path.read_bytes() for path in managed}
    cache = write_pack(manager.data_dir / "runtime" / RUNTIME_PACK_NAME)
    cache_bytes = cache.read_bytes()
    manager.settings_service.save({
        "multiplayer_mode": True, "enable_script_logging": True, "skip_intro_movies": True,
        "enable_exception_probe": True, "custom_battle_all_units_as_lords": True,
        "unit_model_multiplier": 3, "dynamic_ror_compatibility_patch_enabled": True,
        "tier5_compatibility_patch_enabled": True, "voice_compatibility_patch_enabled": True,
    })
    scanned = scan(manager)
    user_id = next(asset.id for asset in manager._assets.values() if asset.path == str(user))
    with (
        patch("backend.api.launch_game", return_value={"pid": 123}) as launch,
        patch("backend.api.ensure_unit_data_patch", side_effect=AssertionError("unit builder called")),
        patch("backend.api.ensure_game_data_patch", side_effect=AssertionError("game builder called")),
        patch("backend.api.ensure_dynamic_ror_compatibility_patch", side_effect=AssertionError("RoR builder called")),
        patch("backend.api.ensure_variant_selector_compatibility_patch", side_effect=AssertionError("variant builder called")),
        patch("backend.api.ensure_tier5_compatibility_patch", side_effect=AssertionError("tier5 builder called")),
        patch("backend.api.ensure_voice_compatibility_patch", side_effect=AssertionError("voice builder called")),
        patch("backend.api.build_runtime_options_pack", side_effect=AssertionError("runtime builder called")),
        patch("backend.api.query_workshop_subscription_status", side_effect=AssertionError("subscription query called")),
    ):
        result = manager.call("launch_game", [[user_id], scanned["order_token"]])
    assert result["ok"], result
    payload = result["data"]
    assert payload["multiplayer_mode"] is True
    assert payload["launch_plan"]["pack_names"] == ["user.pack"]
    assert payload["launch_plan"]["content"] == 'mod "user.pack";\r\n'
    assert launch.call_args.args[1] == payload["launch_plan"]["target_path"]
    assert launch.call_args.args[2] == ""
    assert user.is_file() and disabled.is_file()
    assert cache.read_bytes() == cache_bytes
    for path, content in original.items():
        assert not path.exists()
        assert path.with_name(path.name + DISABLED_PACK_SUFFIX).read_bytes() == content
    record = manager.call("get_last_launch_mods")["data"]
    assert [mod["pack_name"] for mod in record["mods"]] == ["user.pack"]


@pytest.mark.parametrize("method", ["launch_game", "continue_game"])
def test_save_launches_and_empty_mod_lists_use_multiplayer_mode(manager, tmp_path, method):
    game = select_game(manager, tmp_path)
    write_pack(game / "data" / RUNTIME_PACK_NAME, byte_mask=4)
    scan(manager)
    manager.call("set_multiplayer_mode", [True])
    with (
        patch.object(manager.save_games, "latest", return_value={"name": "campaign.save"}),
        patch.object(manager.save_games, "require", return_value={"name": "campaign.save"}),
        patch("backend.api.launch_game", return_value={"pid": 123}) as launch,
    ):
        args = [[], "", "campaign.save"] if method == "launch_game" else [[], ""]
        result = manager.call(method, args)
    assert result["ok"], result
    assert result["data"]["launch_plan"]["pack_names"] == []
    assert launch.call_args.args[2] == "campaign.save"
    assert not (game / "data" / RUNTIME_PACK_NAME).exists()


def test_turning_off_restores_packs_and_normal_launch_options(manager, tmp_path):
    game = select_game(manager, tmp_path)
    marker = write_pack(game / "data" / "wyccc_dynamic_unit_size.pack")
    original = marker.read_bytes()
    manager.settings_service.save({"enable_script_logging": True, "skip_intro_movies": True})
    scanned = scan(manager)
    with (
        patch("backend.api.launch_game", return_value={"pid": 123}),
        patch("backend.api.query_workshop_subscription_status", return_value=[]),
    ):
        manager.call("set_multiplayer_mode", [True])
        multiplayer = manager.call("launch_game", [[], scanned["order_token"]])
        assert multiplayer["ok"], multiplayer
        assert not marker.exists()
        # A second multiplayer launch must keep Packs suspended as well.
        repeated = manager.call("launch_game", [[], multiplayer["data"]["order_token"]])
        assert repeated["ok"], repeated
        assert not marker.exists()
        manager.call("set_multiplayer_mode", [False])
        normal = manager.call("launch_game", [[], repeated["data"]["order_token"]])
    assert normal["ok"], normal
    assert marker.read_bytes() == original
    assert RUNTIME_PACK_NAME in normal["data"]["launch_plan"]["pack_names"]
    assert set(normal["data"]["runtime_options"]["options"]) == {"enable_script_logging", "skip_intro_movies"}


def test_suspension_failure_prevents_game_start(manager, tmp_path):
    game = select_game(manager, tmp_path)
    pack = write_pack(game / "data" / RUNTIME_PACK_NAME)
    scan(manager)
    manager.call("set_multiplayer_mode", [True])
    with (
        patch("backend.multiplayer.Path.replace", side_effect=PermissionError("locked Pack")),
        patch("backend.api.launch_game") as launch,
    ):
        result = manager.call("launch_game", [[]])
    assert not result["ok"]
    launch.assert_not_called()
    assert pack.exists()


def test_rescan_does_not_allow_renamed_runtime_pack_to_return_in_multiplayer(manager, tmp_path):
    game = select_game(manager, tmp_path)
    renamed = write_pack(game / "data" / "renamed.pack", byte_mask=4,
                         entries=[(RUNTIME_OPTIONS_MARKER_ENTRY, b"{}")])
    scan(manager)
    manager.call("set_multiplayer_mode", [True])
    with patch("backend.api.launch_game", return_value={"pid": 123}):
        first = manager.call("launch_game", [[]])
        assert first["ok"], first
        assert not renamed.exists()
        scan(manager)
        assert not any(asset.pack_name == renamed.name for asset in manager._assets.values())
        second = manager.call("launch_game", [[]])
    assert second["ok"], second
    assert not renamed.exists()
    assert renamed.with_name(renamed.name + DISABLED_PACK_SUFFIX).exists()


def test_restore_keeps_newly_rebuilt_pack(tmp_path):
    active = tmp_path / RUNTIME_PACK_NAME
    active.write_bytes(b"new options")
    backup = active.with_name(active.name + DISABLED_PACK_SUFFIX)
    backup.write_bytes(b"old options")
    restore_launcher_packs(str(tmp_path))
    assert active.read_bytes() == b"new options"
    assert not backup.exists()


def test_three_kingdoms_skips_managed_unit_patch_and_preserves_unrelated_wh3_names(manager, tmp_path):
    game = select_game(manager, tmp_path, "three_kingdoms")
    managed = write_pack(game / "data" / UNIT_DATA_PATCH_NAME,
                         entries=[("db\\land_units_tables\\!!!!wyccc_unit_data_v0001", b"generated")])
    ordinary = write_pack(game / "data" / RUNTIME_PACK_NAME)
    scanned = scan(manager)
    ids = [asset.id for asset in manager._assets.values()]
    manager.call("set_multiplayer_mode", [True])
    with (
        patch("backend.api.launch_game", return_value={"pid": 123}),
        patch("backend.api.ensure_unit_data_patch", side_effect=AssertionError("unit builder called")),
    ):
        result = manager.call("launch_game", [ids, scanned["order_token"]])
    assert result["ok"], result
    assert result["data"]["launch_plan"]["pack_names"] == [RUNTIME_PACK_NAME]
    assert ordinary.exists()
    assert not managed.exists()
