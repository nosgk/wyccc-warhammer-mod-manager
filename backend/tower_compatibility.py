"""Launch-time compatibility data for the Warhammer Tower campaign MOD.

The provider only reads enabled MOD Packs. It never edits a MOD Pack and does
not create a second visible compatibility Pack: its entries are appended to the
existing hidden game-data patch Pack.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from .game_data import DbSource, GameDataBuildResult, GameDataEntry


SCHEMA_PATH = Path(__file__).with_name("tower_compatibility_schema.json")
SEP = chr(92)
MARKER = bytes((252, 253, 254, 255))
ONE = bytes((1,))
TOWER_PACK_NAME = "wyccc_roguelike_tower.pack"
EQUIPMENT_CATEGORIES = frozenset(
    {"weapon", "armour", "talisman", "enchanted_item", "arcane_item"}
)
HERO_AGENT_TYPES = frozenset(
    {"champion", "dignitary", "engineer", "runesmith", "spy", "wizard"}
)
TABLES = (
    "campaign_group_unique_agents_tables",
    "unique_agents_tables",
    "ancillaries_tables",
    "ancillaries_included_agent_subtypes_tables",
    "ancillary_to_included_agents_tables",
    "ancillaries_required_skills_tables",
    "main_units_tables",
)
_JUNCTION_TABLES = (
    "ancillaries_included_agent_subtypes_tables",
    "ancillary_to_included_agents_tables",
    "ancillaries_required_skills_tables",
)
_INTERNAL_PACK_MARKERS = (
    "wyccc_roguelike_tower.pack",
    "wyccc_game_data_patch.pack",
    "wyccc_unit_data_patch.pack",
    "wyccc_runtime_options.pack",
)


def _schema() -> dict[str, list[dict[str, Any]]]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _table_key(table: str, values: Mapping[str, Any]) -> str:
    if table == "campaign_group_unique_agents_tables":
        fields = ("campaign_group", "unique_agent")
    elif table == "unique_agents_tables":
        fields = ("agent_subtype",)
    elif table == "main_units_tables":
        fields = ("unit",)
    elif table == "ancillaries_tables":
        fields = ("key",)
    elif table == "ancillaries_included_agent_subtypes_tables":
        fields = ("agent_subtype", "ancillary")
    elif table == "ancillary_to_included_agents_tables":
        fields = ("ancillary", "agent")
    else:
        fields = ("ancillary", "required_skill")
    return chr(31).join(
        "" if values.get(field) is None else str(values.get(field)) for field in fields
    )


def _decode_table(payload: bytes, definitions: Sequence[dict[str, Any]]):
    from .schema_update import decode_table

    return decode_table(payload, definitions)


def _encode_value(value: Any, field_type: Any) -> bytes:
    from .schema_update import _encode_value as encode

    return encode(value, field_type)


def _default(field: Mapping[str, Any]) -> Any:
    from .schema_update import _default as default

    return default(dict(field))


def _encode_row(values: Mapping[str, Any], definition: Mapping[str, Any]) -> bytes:
    return b"".join(
        _encode_value(values.get(field["name"], _default(dict(field))), field["type"])
        for field in definition["fields"]
    )


def _encode_table(definition: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (
            MARKER,
            struct.pack("<i", int(definition["version"])),
            ONE,
            struct.pack("<i", len(rows)),
            *(_encode_row(row, definition) for row in rows),
        )
    )


@dataclass(frozen=True)
class _EffectiveRow:
    values: Mapping[str, Any]
    definition: Mapping[str, Any]
    source_is_mod: bool
    source_name: str


def _source_pack_name(source: DbSource) -> str:
    text = source.name
    start = text.casefold().find("[pack: ")
    if start < 0:
        return text.casefold()
    end = text.find("]", start)
    if end < 0:
        return text.casefold()
    return text[start + 7 : end].strip().casefold()


def _source_is_mod(source: DbSource) -> bool:
    if str(source.role).casefold() in {"vanilla", "unit_data_patch"}:
        return False
    name = _source_pack_name(source)
    return not any(marker in name for marker in _INTERNAL_PACK_MARKERS)


def _collect_effective(sources: Sequence[DbSource]) -> tuple[dict[str, dict[str, _EffectiveRow]], set[str]]:
    schema = _schema()
    effective: dict[str, dict[str, _EffectiveRow]] = {table: {} for table in TABLES}
    failed: set[str] = set()
    for source in sources:
        is_mod = _source_is_mod(source)
        for entry in source.entries:
            name = entry.name.replace("/", SEP)
            parts = name.split(SEP)
            if len(parts) < 3 or parts[0].casefold() != "db":
                continue
            table = parts[1].casefold()
            definitions = schema.get(table)
            if not definitions:
                continue
            try:
                definition, rows, _ = _decode_table(entry.payload, definitions)
            except (ValueError, TypeError):
                failed.add(table)
                continue
            for cells in rows:
                values = {
                    field["name"]: cell[0]
                    for field, cell in zip(definition["fields"], cells)
                }
                key = _table_key(table, values)
                current = effective[table].get(key)
                if current is None or (
                    (is_mod and not current.source_is_mod)
                    or (is_mod == current.source_is_mod)
                ):
                    effective[table][key] = _EffectiveRow(
                        values, definition, is_mod, source.name
                    )
    return effective, failed


def _version_zero(schema: Mapping[str, Sequence[dict[str, Any]]], table: str) -> dict[str, Any]:
    return next(item for item in schema[table] if int(item["version"]) == 0)


def _priority_prefix(sources: Sequence[DbSource]) -> str:
    maximum = 0
    for source in sources:
        for entry in source.entries:
            internal = entry.name.replace("/", SEP).rsplit(SEP, 1)[-1]
            maximum = max(maximum, len(internal) - len(internal.lstrip("!")))
    # The game-data builder adds one marker above its source tables. Two extra
    # markers keep the Tower overrides authoritative inside the same Pack.
    return "!" * (maximum + 2)


def _append_table(
    entries: list[GameDataEntry],
    table: str,
    rows: Sequence[Mapping[str, Any]],
    definition: Mapping[str, Any],
    prefix: str,
    suffix: str = "",
) -> None:
    if not rows:
        return
    name = SEP.join(("db", table, f"{prefix}wyccc_tower_compat_{table}{suffix}"))
    entries.append(GameDataEntry(name, _encode_table(definition, rows)))


def _append_grouped(
    entries: list[GameDataEntry],
    table: str,
    grouped: Mapping[int, list[Mapping[str, Any]]],
    schema: Mapping[str, Sequence[dict[str, Any]]],
    prefix: str,
) -> int:
    total = 0
    for version, rows in sorted(grouped.items()):
        definition = next(
            (item for item in schema[table] if int(item["version"]) == int(version)),
            schema[table][0],
        )
        _append_table(entries, table, rows, definition, prefix, f"_v{int(version):04d}")
        total += len(rows)
    return total


def build_tower_compatibility_entries(sources: Sequence[DbSource]) -> GameDataBuildResult:
    """Return only the DB entries needed by the Tower compatibility toggle."""
    schema = _schema()
    effective, failed = _collect_effective(sources)
    stats: dict[str, int] = {
        "tower_unique_agents_added": 0,
        "tower_mod_equipment_unlocked": 0,
        "tower_equipment_restriction_rows_removed": 0,
        "tower_mod_unit_caps_removed": 0,
    }
    entries: list[GameDataEntry] = []
    prefix = _priority_prefix(sources)

    # 1. MOD unique heroes get a cross-culture dormant registration. It grants
    # no charges; the Tower shop's native force spawn uses it.
    existing = {
        str(row.values.get("unique_agent") or "")
        for row in effective["campaign_group_unique_agents_tables"].values()
        if str(row.values.get("campaign_group") or "") == "wh_main_feature_all"
    }
    new_agents: list[Mapping[str, Any]] = []
    for row in effective["unique_agents_tables"].values():
        if not row.source_is_mod:
            continue
        subtype = str(row.values.get("agent_subtype") or "")
        agent_type = str(row.values.get("agent_type") or "").casefold()
        if not subtype or subtype in existing or agent_type not in HERO_AGENT_TYPES:
            continue
        new_agents.append(
            {
                "campaign_group": "wh_main_feature_all",
                "unique_agent": subtype,
                "base_charges": 0,
            }
        )
        existing.add(subtype)
    if new_agents:
        definition = schema["campaign_group_unique_agents_tables"][0]
        _append_table(entries, "campaign_group_unique_agents_tables", new_agents, definition, prefix)
        stats["tower_unique_agents_added"] = len(new_agents)

    # 2. MOD equipment loses faction/subtype/skill restrictions. Only changed
    # rows are written; native equipment remains untouched.
    mod_equipment: set[str] = set()
    unlocked: dict[int, list[Mapping[str, Any]]] = {}
    for row in effective["ancillaries_tables"].values():
        if not row.source_is_mod:
            continue
        category = str(row.values.get("category") or "").casefold()
        if category not in EQUIPMENT_CATEGORIES:
            continue
        key = str(row.values.get("key") or "")
        if not key:
            continue
        mod_equipment.add(key)
        values = dict(row.values)
        changed = False
        if "faction_set" in values and str(values.get("faction_set") or "") != "all":
            values["faction_set"] = "all"
            changed = True
        if "unique_to_faction" in values and values.get("unique_to_faction") is not False:
            values["unique_to_faction"] = False
            changed = True
        if "transferrable" in values and values.get("transferrable") is not True:
            values["transferrable"] = True
            changed = True
        if "applies_to" in values and str(values.get("applies_to") or "") != ".":
            values["applies_to"] = "."
            changed = True
        if changed:
            unlocked.setdefault(int(row.definition["version"]), []).append(values)
    if unlocked:
        stats["tower_mod_equipment_unlocked"] = _append_grouped(
            entries, "ancillaries_tables", unlocked, schema, prefix
        )

    # 3. Same-path junction tables must be replaced in full so the original
    # user/subtype/skill whitelist rows disappear.
    for table in _JUNCTION_TABLES:
        rows = effective[table]
        if not rows or table in failed:
            continue
        kept = [
            row.values
            for row in rows.values()
            if str(row.values.get("ancillary") or "") not in mod_equipment
        ]
        removed = len(rows) - len(kept)
        if removed:
            _append_table(entries, table, kept, _version_zero(schema, table), prefix)
            stats["tower_equipment_restriction_rows_removed"] += removed

    # 4. MOD unit recruitment caps become unlimited. Do not touch native units.
    changed_caps: dict[int, list[Mapping[str, Any]]] = {}
    for row in effective["main_units_tables"].values():
        if not row.source_is_mod:
            continue
        values = dict(row.values)
        cap = int(values.get("campaign_cap") or 0)
        if cap <= 0:
            continue
        values["campaign_cap"] = -1
        changed_caps.setdefault(int(row.definition["version"]), []).append(values)
    if changed_caps:
        stats["tower_mod_unit_caps_removed"] = _append_grouped(
            entries, "main_units_tables", changed_caps, schema, prefix
        )

    return GameDataBuildResult(tuple(entries), stats)
