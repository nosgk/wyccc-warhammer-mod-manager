from __future__ import annotations

import os
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from backend.constants import VOICE_COMPATIBILITY_PATCH_NAME
from backend.game_data import DbSource, GameDataEntry
from backend.start_options import read_pack_entries
from backend.voice_compatibility import (
    DecisionNode, DialogueEvent, VOICE_RUNTIME_MARKER, WH3_BANK_VERSION,
    build_voice_compatibility_entries, encode_dialogue_event, event_paths,
    parse_dialogue_event, parse_soundbank,
)
from backend.voice_patch_state import ensure_voice_compatibility_patch
from tests.helpers import make_asset, write_pack

PREFIX = "audio\\wwise\\"
BASE_NAME = PREFIX + "english(uk)\\campaign_vo__core.bnk"


def event(values, *, event_id=100, arguments=(123,), mode=0):
    def node(key, value):
        if isinstance(value, dict):
            return DecisionNode(key, 50, 100, children=tuple(node(k, v) for k, v in sorted(value.items())))
        return DecisionNode(key, 50, 100, audio_id=value)

    return DialogueEvent(event_id, 100, arguments, bytes([1] * len(arguments)), mode,
                         node(0, values), b"\0\0")


def bank(events, *, bank_id=1000, extras=(), version=WH3_BANK_VERSION):
    objects = [*(struct.pack("<BI", 15, len(raw)) + raw for raw in map(encode_dialogue_event, events)),
               *(struct.pack("<BI", kind, len(payload)) + payload for kind, payload in extras)]
    header = struct.pack("<6I", version, bank_id, 550298558, 16, 2361, 0)
    hirc = struct.pack("<I", len(objects)) + b"".join(objects)
    return b"BKHD" + struct.pack("<I", len(header)) + header + b"HIRC" + struct.pack("<I", len(hirc)) + hirc


def source(name, values, *, role="mod", filename=None, **kwargs):
    filename = filename or PREFIX + name + ".bnk"
    return DbSource(name, (GameDataEntry(filename, bank([event(values, **kwargs)])),), role)


def merged_events(entries):
    return {parse_soundbank(entry.payload).dialogues()[100] for entry in entries}


def test_binary_tree_layout_with_nested_and_early_terminal_paths():
    # Independent byte fixture, including an early terminal and explicit silence.
    nodes = b"".join([
        struct.pack("<IHHHH", 0, 1, 2, 50, 100),
        struct.pack("<IHHHH", 10, 3, 2, 17, 99),
        struct.pack("<IIHH", 20, 0xABCDEF01, 18, 97),
        struct.pack("<IIHH", 0, 0xABCDEF02, 19, 96),
        struct.pack("<IIHH", 7, 0, 20, 95),
    ])
    payload = struct.pack("<IBI2I2BIB", 100, 90, 2, 123, 456, 1, 0, len(nodes), 1) + nodes + b"\0\0"
    parsed = parse_dialogue_event(payload)
    assert event_paths(parsed) == {(10, 0): (0xABCDEF02, 19, 96), (10, 7): (0, 20, 95),
                                  (20,): (0xABCDEF01, 18, 97)}
    assert encode_dialogue_event(parsed) == payload


def test_full_banks_keep_both_mod_deltas_and_current_vanilla_paths():
    entries, stats = build_voice_compatibility_entries([
        source("high", {1: 111, 2: 20, 3: 300}),
        source("low", {1: 10, 2: 222, 4: 400}),
        source("vanilla", {1: 10, 2: 20, 5: 50}, role="vanilla"),
    ])
    merged, = merged_events(entries)
    assert {path: value[0] for path, value in event_paths(merged).items()} == {
        (1,): 111, (2,): 222, (3,): 300, (4,): 400, (5,): 50,
    }
    assert stats["merged_event_count"] == 1 and stats["conflicting_path_count"] == 0


def test_three_mods_same_branch_uses_first_mod_and_reversing_order_changes_winner():
    high, low = source("high", {1: 111}), source("low", {1: 222})
    third, base = source("third", {2: 333}), source("base", {1: 10}, role="vanilla")
    for sources, winner in [([high, low, third, base], 111), ([low, high, third, base], 222)]:
        entries, stats = build_voice_compatibility_entries(sources)
        merged, = merged_events(entries)
        assert event_paths(merged) == {(1,): (winner, 50, 100), (2,): (333, 50, 100)}
        assert stats["conflicting_path_count"] == 1


def test_authoring_tree_wins_over_testing_copy_inside_the_same_mod():
    full = source("high", {1: 999}, filename=PREFIX + "a_for_testing.bnk")
    explicit = source("high", {1: 10}, filename=PREFIX + "z_for_merging.bnk")
    combined = DbSource("high", (*full.entries, *explicit.entries))
    entries, _ = build_voice_compatibility_entries([
        combined, source("low", {1: 222}), source("base", {1: 10}, role="vanilla"),
    ])
    merged, = merged_events(entries)
    assert event_paths(merged)[(1,)][0] == 10


def test_shared_file_path_merges_dialogues_and_retains_lower_unique_events():
    name = PREFIX + "shared.bnk"
    high = DbSource("high", (GameDataEntry(name, bank([event({1: 111})])),))
    low = DbSource("low", (GameDataEntry(name, bank([event({2: 222}), event({3: 333}, event_id=200)])),))
    entries, _ = build_voice_compatibility_entries([high, low])
    assert len(entries) == 1
    events = parse_soundbank(entries[0].payload).dialogues()
    assert event_paths(events[100]) == {(1,): (111, 50, 100), (2,): (222, 50, 100)}
    assert event_paths(events[200]) == {(3,): (333, 50, 100)}


def test_shared_file_does_not_silently_lose_playback_objects():
    name = PREFIX + "shared.bnk"
    high = source("high", {1: 111}, filename=name)
    low = DbSource("low", (GameDataEntry(name, bank([event({2: 222})], extras=[(2, b"abcdpayload")])),))
    with pytest.raises(ValueError, match="播放对象"):
        build_voice_compatibility_entries([high, low])


def test_shared_file_keeps_higher_priority_playback_bank_when_adding_missing_dialogues():
    name = PREFIX + "shared.bnk"
    original = bank([], bank_id=999, extras=[(2, b"abcdsound")])
    high = DbSource("high", (GameDataEntry(name, original),))
    low = source("low", {1: 111}, filename=name)
    entries, _ = build_voice_compatibility_entries([high, low])
    assert len(entries) == 1
    actual = parse_soundbank(entries[0].payload)
    assert actual.chunks[0] == parse_soundbank(original).chunks[0]
    assert (2, b"abcdsound") in actual.objects
    assert event_paths(actual.dialogues()[100]) == {(1,): (111, 50, 100)}


def test_bank_header_and_non_dialogue_objects_are_unchanged():
    original = bank([event({1: 111})], bank_id=123456, extras=[(2, b"abcdsound"), (5, b"efghcontainer")])
    high = DbSource("high", (GameDataEntry(PREFIX + "high.bnk", original),))
    entries, _ = build_voice_compatibility_entries([high, source("low", {2: 222})])
    actual, parsed = parse_soundbank(entries[0].payload), parse_soundbank(original)
    assert actual.chunks[0] == parsed.chunks[0]
    assert [(k, p) for k, p in actual.objects if k != 15] == [(k, p) for k, p in parsed.objects if k != 15]


def test_language_scopes_are_isolated():
    entries, stats = build_voice_compatibility_entries([
        source("cn1", {1: 111}, filename=PREFIX + "chinese\\one.bnk"),
        source("cn2", {2: 222}, filename=PREFIX + "chinese\\two.bnk"),
        source("en", {1: 999}, filename=PREFIX + "english(uk)\\one.bnk"),
    ])
    assert len(entries) == 2 and stats["merged_event_count"] == 1
    assert all("\\chinese\\" in entry.name for entry in entries)


def test_mixed_global_and_localized_event_layout_is_reported_instead_of_silently_skipped():
    with pytest.raises(ValueError, match="混合布局"):
        build_voice_compatibility_entries([
            source("global", {1: 111}), source("local", {2: 222}, filename=PREFIX + "chinese\\local.bnk"),
        ])


@pytest.mark.parametrize("broken", [b"", b"BKHD\x18\0\0\0short", bank([event({1: 111})])[:-1]])
def test_rejects_truncated_banks(broken):
    with pytest.raises(ValueError):
        parse_soundbank(broken)


def test_rejects_incompatible_versions_and_argument_structures():
    with pytest.raises(ValueError, match="0x80000088"):
        parse_soundbank(bank([event({1: 111})], version=135))
    with pytest.raises(ValueError, match="参数结构"):
        build_voice_compatibility_entries([source("one", {1: 111}), source("two", {2: 222}, arguments=(456,))])


def test_rejects_invalid_tree_ranges_duplicate_keys_and_overflow():
    valid = encode_dialogue_event(event({1: 111, 2: 222}))
    for index, value in [(14, 99999), (23, 0), (43, 1)]:
        broken = bytearray(valid)
        struct.pack_into("<I", broken, index, value)
        with pytest.raises(ValueError):
            parse_dialogue_event(bytes(broken))
    oversized = replace(event({1: 111}), root=DecisionNode(0, 50, 100, children=tuple(
        DecisionNode(i, 50, 100, audio_id=111) for i in range(65536)
    )))
    with pytest.raises(ValueError, match="容量"):
        encode_dialogue_event(oversized)


def prepare(root):
    data = root / "data"
    write_pack(data / "audio_en_bnk.pack", entries=[(BASE_NAME, bank([event({1: 10, 2: 20})]))])
    assets = {}
    for name, values in [("high", {1: 111, 2: 20}), ("low", {1: 10, 2: 222})]:
        path = write_pack(root / "mods" / f"{name}.pack", byte_mask=3,
                          entries=[(PREFIX + name + ".bnk", bank([event(values)])),
                                   (PREFIX + name + ".wem", b"unmodified audio")])
        assets[name] = make_asset(path, name, "workshop")
    return data, assets


def ensure(root, data, assets, ids=None, enabled=True, playset="playset", subscribed=True):
    return ensure_voice_compatibility_patch(root / "runtime", data, assets,
                                           list(assets) if ids is None else ids, playset,
                                           {"voice_compatibility_patch_enabled": enabled}, subscribed=subscribed)


def test_cache_tampering_source_content_order_playset_and_stale_cleanup(tmp_path):
    data, assets = prepare(tmp_path)
    generated = ensure(tmp_path, data, assets)
    assert generated["status"] == "generated"
    output = Path(generated["path"])
    assert all(entry.name.endswith(".bnk") or entry.name == VOICE_RUNTIME_MARKER
               for entry in read_pack_entries(output))
    assert ensure(tmp_path, data, assets)["status"] == "reused"
    output.write_bytes(b"bad cache")
    assert ensure(tmp_path, data, assets)["status"] == "generated"
    source_path = Path(assets["high"].path)
    before = source_path.stat()
    source_path.write_bytes(source_path.read_bytes().replace(struct.pack("<I", 111), struct.pack("<I", 333)))
    os.utime(source_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = ensure(tmp_path, data, assets)
    assert changed["status"] == "generated" and changed["fingerprint"] != generated["fingerprint"]
    reordered = ensure(tmp_path, data, assets, ids=["low", "high"])
    assert reordered["fingerprint"] != changed["fingerprint"]
    assert ensure(tmp_path, data, assets, playset="other")["status"] == "generated"
    assert ensure(tmp_path, data, assets, ids=["high"])["status"] == "zero_modification"
    assert not output.exists()
    assert ensure(tmp_path, data, assets)["path"]
    assert ensure(tmp_path, data, assets, enabled="false")["reason"] == "disabled"
    assert not output.exists()


def test_auto_movie_participates_but_disabled_normal_mod_and_old_runtime_do_not(tmp_path):
    data, assets = prepare(tmp_path)
    movie = write_pack(data / "movie.pack", byte_mask=4,
                       entries=[(PREFIX + "movie.bnk", bank([event({3: 333})]))])
    generated = ensure(tmp_path, data, assets, ids=["high"])
    assert generated["stats"]["voice_pack_count"] == 2
    stale = write_pack(data / "renamed_old_runtime.pack", byte_mask=4,
                       entries=[(VOICE_RUNTIME_MARKER, b"{}"), (PREFIX + "stale.bnk", bank([event({4: 444})]))])
    assert ensure(tmp_path, data, assets, ids=["high"])["fingerprint"] == generated["fingerprint"]
    movie.unlink()
    assert ensure(tmp_path, data, assets, ids=["high"])["status"] == "zero_modification"
    assert stale.is_file()


def test_unused_language_banks_need_not_be_installed(tmp_path):
    data, assets = prepare(tmp_path)
    for name, asset in assets.items():
        values = {1: 111, 2: 20} if name == "high" else {1: 10, 2: 222}
        write_pack(Path(asset.path), byte_mask=3, entries=[
            (PREFIX + "chinese\\" + name + ".bnk", bank([event(values)])),
        ])
    assert not (data / "audio_cn_bnk.pack").exists()
    generated = ensure(tmp_path, data, assets)
    assert generated["status"] == "generated"
    assert generated["stats"]["merged_path_count"] == 2


def test_missing_vanilla_and_continuously_changing_sources_fail_before_writing(tmp_path, monkeypatch):
    from backend import voice_patch_state

    data, assets = prepare(tmp_path)
    original_snapshot = voice_patch_state._snapshot
    calls = 0

    def changing(*args):
        nonlocal calls
        sources, records = original_snapshot(*args)
        calls += 1
        records.append({"changed": calls})
        return sources, records

    monkeypatch.setattr(voice_patch_state, "_snapshot", changing)
    with pytest.raises(ValueError, match="连续变化"):
        ensure(tmp_path, data, assets)
    assert not (tmp_path / "runtime" / VOICE_COMPATIBILITY_PATCH_NAME).exists()
    monkeypatch.setattr(voice_patch_state, "_snapshot", original_snapshot)
    (data / "audio_en_bnk.pack").unlink()
    with pytest.raises(ValueError, match="原版对话事件库"):
        ensure(tmp_path, data, assets)


def test_mock_launch_stages_hidden_patch_before_mods_then_removes_it(tmp_path, monkeypatch):
    from backend import api as api_module
    from tests.test_storage_and_api import ApiContractTests

    api, _ = ApiContractTests._prepare_launch_api(tmp_path)
    try:
        monkeypatch.setattr(api_module, "get_subscribed_workshop_items", lambda **kwargs: [api_module.VOICE_FEATURE_WORKSHOP_ID])
        data = tmp_path / "Total War WARHAMMER III/data"
        fixture_data, assets = prepare(tmp_path / "fixtures")
        (data / "audio_en_bnk.pack").write_bytes((fixture_data / "audio_en_bnk.pack").read_bytes())
        for asset in assets.values():
            (data / asset.pack_name).write_bytes(Path(asset.path).read_bytes())
        scan = api.call("scan_mods", [False])
        assert scan["ok"], scan
        ids = [mod["id"] for mod in scan["data"]["mods"] if mod["pack_name"] in {"high.pack", "low.pack"}]
        preference = api.call("save_compatibility_patch_settings", [{"voice_compatibility_patch_enabled": "true"}])
        assert preference["ok"] and preference["data"]["settings"]["voice_compatibility_patch_enabled"]
        monkeypatch.setattr(api_module, "launch_game", lambda *args, **kwargs: {"pid": 123})
        monkeypatch.setattr(api_module, "is_game_running", lambda *args, **kwargs: False)
        monkeypatch.setattr(api_module, "query_workshop_subscription_status", lambda *args, **kwargs: [])
        monkeypatch.setattr(api, "set_game_running", lambda *args, **kwargs: None)
        launch = api.call("launch_game", [ids, scan["data"]["order_token"]])
        assert launch["ok"], launch
        assert launch["data"]["voice_compatibility_patch"]["status"] == "generated"
        assert launch["data"]["launch_plan"]["ordered_mod_ids"] == ["runtime:voice-compatibility", *ids]
        assert (data / VOICE_COMPATIBILITY_PATCH_NAME).is_file()
        scan = api.call("scan_mods", [False])
        patch = next(mod for mod in scan["data"]["mods"] if mod["pack_name"] == VOICE_COMPATIBILITY_PATCH_NAME)
        assert patch["hidden"]
        assert api.call("save_compatibility_patch_settings", [{"voice_compatibility_patch_enabled": False}])["ok"]
        launch = api.call("launch_game", [ids, scan["data"]["order_token"]])
        assert launch["ok"], launch
        assert launch["data"]["launch_plan"]["ordered_mod_ids"] == ids
        assert not (data / VOICE_COMPATIBILITY_PATCH_NAME).exists()
        assert not (api.data_dir / "runtime" / VOICE_COMPATIBILITY_PATCH_NAME).exists()
    finally:
        api.close()


def test_subscription_gate_removes_stale_patch_even_with_saved_setting_enabled(tmp_path):
    data, assets = prepare(tmp_path)
    built = ensure(tmp_path, data, assets)
    assert Path(built["path"]).is_file()
    unavailable = ensure(tmp_path, data, assets, subscribed=False)
    assert unavailable["reason"] == "patch_not_subscribed" and not unavailable["path"]
    assert not Path(built["path"]).exists()
    assert ensure(tmp_path, data, assets)["status"] == "generated"


def test_api_requires_current_account_subscription_not_local_marker_files(tmp_path, monkeypatch):
    from backend import api as api_module
    from backend.api import API
    from backend.constants import VOICE_FEATURE_PACK_NAME
    from backend.models import GamePaths
    from backend.scanner import is_internal_feature_mod
    from backend.steamworks_bridge import SteamworksBridgeError

    api = API(tmp_path / "state")
    try:
        # Both a local shell and another account's downloaded copy are insufficient.
        write_pack(tmp_path / "data" / VOICE_FEATURE_PACK_NAME)
        write_pack(tmp_path / "workshop" / "111" / VOICE_FEATURE_PACK_NAME)
        monkeypatch.setattr(api_module, "get_subscribed_workshop_items", lambda **kwargs: [])
        result = api.call("save_compatibility_patch_settings", [{"voice_compatibility_patch_enabled": True}])
        assert result["ok"] and not result["data"]["settings"]["voice_compatibility_patch_enabled"]
        monkeypatch.setattr(api_module, "get_subscribed_workshop_items", lambda **kwargs: [api_module.VOICE_FEATURE_WORKSHOP_ID])
        assert api.call("get_voice_feature_status")["data"]["subscribed"]
        result = api.call("save_compatibility_patch_settings", [{"voice_compatibility_patch_enabled": True}])
        assert result["data"]["settings"]["voice_compatibility_patch_enabled"]

        def unavailable(**kwargs):
            raise SteamworksBridgeError("offline fixture")

        monkeypatch.setattr(api_module, "get_subscribed_workshop_items", unavailable)
        assert not api.call("get_voice_feature_status")["data"]["subscribed"]
        assert not api._voice_feature_status(GamePaths(game_id="three_kingdoms"))["subscribed"]
        asset = make_asset(tmp_path / "data" / VOICE_FEATURE_PACK_NAME, "marker", "data")
        assert is_internal_feature_mod(asset, "warhammer3")
        assert not is_internal_feature_mod(asset, "three_kingdoms")
    finally:
        api.close()
