from __future__ import annotations

from pathlib import Path

from backend.game_data import DbSource, GameDataEntry
from backend.schema_update import decode_table
from backend.start_options import (
    GameDataSourceSnapshot,
    GameDataSourceSnapshotEntry,
    GameDataSourceSpec,
    _effective_game_data_settings,
    _enabled_game_data_options,
    _game_data_enabled,
    build_game_data_patch,
)
from backend.tower_compatibility import (
    _encode_table,
    _schema,
    build_tower_compatibility_entries,
)

SEP = chr(92)


def entry(table: str, version: int, rows: list[dict]) -> GameDataEntry:
    schema = _schema()
    definition = next(item for item in schema[table] if int(item["version"]) == version)
    return GameDataEntry(
        SEP.join(("db", table, "fixture")),
        _encode_table(definition, rows),
    )


def decode(entry_value: GameDataEntry) -> list[dict]:
    table = entry_value.name.split(SEP)[1]
    definition, rows, _ = decode_table(entry_value.payload, _schema()[table])
    return [
        {field["name"]: cell[0] for field, cell in zip(definition["fields"], row)}
        for row in rows
    ]


def sources() -> list[DbSource]:
    mod = [
        entry("unique_agents_tables", 3, [
            {"agent_subtype": "nvxia", "agent_type": "spy"},
            {"agent_subtype": "guojin", "agent_type": "champion"},
        ]),
        entry("campaign_group_unique_agents_tables", 0, []),
        entry("ancillaries_tables", 0, [
            {"key": "mod_sword", "type": "weapon", "category": "weapon",
             "applies_to": "empire", "transferrable": False, "faction_set": "empire"},
        ]),
        entry("ancillaries_included_agent_subtypes_tables", 0, [
            {"agent_subtype": "empire_hero", "ancillary": "mod_sword"},
            {"agent_subtype": "native_hero", "ancillary": "native_sword"},
        ]),
        entry("ancillary_to_included_agents_tables", 0, [
            {"ancillary": "mod_sword", "agent": "champion"},
        ]),
        entry("ancillaries_required_skills_tables", 0, [
            {"ancillary": "mod_sword", "required_skill": "mod_skill", "required_skill_level": 1},
        ]),
        entry("main_units_tables", 7, [
            {"unit": "mod_unit", "campaign_cap": 2, "land_unit": "mod_land"},
        ]),
    ]
    vanilla = [
        entry("unique_agents_tables", 3, [
            {"agent_subtype": "native_hero", "agent_type": "champion"},
        ]),
        entry("campaign_group_unique_agents_tables", 0, [
            {"campaign_group": "wh3_main_feature_cathay", "unique_agent": "native_hero",
             "base_charges": 1},
        ]),
        entry("ancillaries_tables", 0, [
            {"key": "native_sword", "type": "weapon", "category": "weapon",
             "applies_to": "empire", "transferrable": False, "faction_set": "empire"},
        ]),
        entry("ancillaries_included_agent_subtypes_tables", 0, [
            {"agent_subtype": "native_hero", "ancillary": "native_sword"},
        ]),
        entry("ancillary_to_included_agents_tables", 0, [
            {"ancillary": "native_sword", "agent": "champion"},
        ]),
        entry("ancillaries_required_skills_tables", 0, [
            {"ancillary": "native_sword", "required_skill": "native_skill",
             "required_skill_level": 1},
        ]),
        entry("main_units_tables", 7, [
            {"unit": "native_unit", "campaign_cap": 2, "land_unit": "native_land"},
        ]),
    ]
    return [
        DbSource("MOD test [pack: test_mod.pack]", tuple(mod), role="explicit"),
        DbSource("vanilla [pack: db.pack]", tuple(vanilla), role="vanilla"),
    ]


def test_tower_compatibility_adds_only_mod_unique_agents_equipment_and_caps():
    result = build_tower_compatibility_entries(sources())
    assert result.stats == {
        "tower_unique_agents_added": 2,
        "tower_mod_equipment_unlocked": 1,
        "tower_equipment_restriction_rows_removed": 3,
        "tower_mod_unit_caps_removed": 1,
    }
    by_table = {}
    for item in result.entries:
        by_table.setdefault(item.name.split(SEP)[1], []).extend(decode(item))

    assert {row["unique_agent"] for row in by_table["campaign_group_unique_agents_tables"]} == {
        "nvxia", "guojin"
    }
    assert all(row["campaign_group"] == "wh_main_feature_all"
               and row["base_charges"] == 0
               for row in by_table["campaign_group_unique_agents_tables"])

    assert len(by_table["ancillaries_tables"]) == 1
    sword = by_table["ancillaries_tables"][0]
    assert sword["key"] == "mod_sword"
    assert sword["faction_set"] == "all"
    assert sword["transferrable"] is True
    assert sword["applies_to"] == "."

    for table in (
        "ancillaries_included_agent_subtypes_tables",
        "ancillary_to_included_agents_tables",
        "ancillaries_required_skills_tables",
    ):
        assert {row["ancillary"] for row in by_table[table]} == {"native_sword"}

    caps = {row["unit"]: row["campaign_cap"] for row in by_table["main_units_tables"]}
    assert caps == {"mod_unit": -1}


def test_tower_compatibility_is_a_game_data_option_without_a_separate_pack():
    settings = _effective_game_data_settings(
        {"tower_compatibility_patch_enabled": True}, []
    )
    assert settings["tower_compatibility_patch_enabled"] is True
    assert _game_data_enabled(settings) is True
    assert _enabled_game_data_options(settings) == ["tower_compatibility_patch_enabled"]


def snapshot() -> GameDataSourceSnapshot:
    entries = []
    for source in sources():
        role = "vanilla" if str(source.role).casefold() == "vanilla" else "explicit"
        entries.append(
            GameDataSourceSnapshotEntry(
                spec=GameDataSourceSpec(
                    path=Path(source.name.split("[pack: ")[-1].rstrip("]")),
                    asset=None,
                    role=role,
                ),
                source=source,
                size=0,
                mtime_ns=0,
                content_sha256="",
            )
        )
    return GameDataSourceSnapshot(tuple(entries))


def test_game_data_patch_pack_contains_tower_entries_without_a_second_pack(tmp_path):
    result = build_game_data_patch(
        tmp_path,
        str(tmp_path / "data"),
        {},
        [],
        {"tower_compatibility_patch_enabled": True},
        source_snapshot=snapshot(),
    )
    assert result["path"].endswith("!!!!wyccc_game_data_patch.pack")
    assert result["options"] == ["tower_compatibility_patch_enabled"]
    assert result["entry_count"] == 6
    assert result["game_data"]["tower_unique_agents_added"] == 2


def test_rpc_recognizes_tower_by_display_name_or_workshop_id(tmp_path):
    from tests.helpers import write_pack
    from tests.test_storage_and_api import ApiContractTests

    api, _scan = ApiContractTests._prepare_launch_api(tmp_path)
    data = tmp_path / "Total War WARHAMMER III" / "data"
    write_pack(data / "renamed_tower_mod.pack")
    scan = api.call("scan_mods", [False])
    tower = next(
        mod for mod in scan["data"]["mods"]
        if mod["pack_name"] == "renamed_tower_mod.pack"
    )
    api._assets[tower["id"]].display_name = "Standalone Roguelike STS Mode"
    saved = api.call("save_load_order", [[tower["id"]], scan["data"]["order_token"]])
    assert saved["ok"]
    result = api.call(
        "save_compatibility_patch_settings",
        [{"tower_compatibility_patch_enabled": True}],
    )
    assert result["ok"]
    assert result["data"]["settings"]["tower_compatibility_patch_enabled"]


def test_rpc_enables_tower_option_only_when_tower_pack_is_active(tmp_path):
    from tests.helpers import write_pack
    from tests.test_storage_and_api import ApiContractTests

    api, _scan = ApiContractTests._prepare_launch_api(tmp_path)
    data = tmp_path / "Total War WARHAMMER III" / "data"
    write_pack(data / "wyccc_roguelike_tower.pack")
    scan = api.call("scan_mods", [False])
    assert scan["ok"]
    tower = next(
        mod for mod in scan["data"]["mods"]
        if mod["pack_name"] == "wyccc_roguelike_tower.pack"
    )
    saved = api.call("save_load_order", [[tower["id"]], scan["data"]["order_token"]])
    assert saved["ok"]
    result = api.call(
        "save_compatibility_patch_settings",
        [{"tower_compatibility_patch_enabled": True}],
    )
    assert result["ok"]
    assert result["data"]["settings"]["tower_compatibility_patch_enabled"]
