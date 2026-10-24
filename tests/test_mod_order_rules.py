from __future__ import annotations

import unittest

from backend.mod_order_rules import (
    HARD,
    SOFT,
    ModOrderEvidence,
    OrderRelation,
    build_category_relations,
    detect_conflicts,
    evaluate_order,
    stable_topological_order,
)


def evidence(
    mod_id: str,
    *,
    pack_name: str | None = None,
    name: str | None = None,
    description: str = "",
    workshop_id: str = "",
    required_workshop_items: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    db_tables: tuple[str, ...] = (),
    script_paths: tuple[str, ...] = (),
    localization_paths: tuple[str, ...] = (),
    ui_paths: tuple[str, ...] = (),
    variant_paths: tuple[str, ...] = (),
    unit_data_tables: tuple[str, ...] = (),
    provides_variant_selector: bool = False,
) -> ModOrderEvidence:
    return ModOrderEvidence(
        mod_id=mod_id,
        name=name or mod_id,
        pack_name=pack_name or f"{mod_id}.pack",
        workshop_id=workshop_id,
        description=description,
        required_workshop_items=required_workshop_items,
        dependencies=dependencies,
        db_tables=db_tables,
        script_paths=script_paths,
        localization_paths=localization_paths,
        ui_paths=ui_paths,
        variant_paths=variant_paths,
        unit_data_tables=unit_data_tables,
        provides_variant_selector=provides_variant_selector,
    )


class DependencyTests(unittest.TestCase):
    def test_required_workshop_item_moves_dependency_first(self) -> None:
        items = [
            evidence("a", workshop_id="100", required_workshop_items=("200",)),
            evidence("b", workshop_id="200"),
        ]
        result = evaluate_order(items, ["a", "b"])
        self.assertEqual(result.recommended_order, ["b", "a"])
        self.assertTrue(any(relation.kind == SOFT for relation in result.relations))

    def test_pack_dependency_moves_dependency_first(self) -> None:
        items = [
            evidence("a", dependencies=("base.pack",)),
            evidence("b", pack_name="base.pack"),
        ]
        result = evaluate_order(items, ["a", "b"])
        self.assertEqual(result.recommended_order, ["b", "a"])
        self.assertTrue(any(relation.kind == HARD for relation in result.relations))

    def test_missing_dependency_is_reported_without_subscribing(self) -> None:
        items = [evidence("a", required_workshop_items=("999",))]
        result = evaluate_order(items, ["a"])
        self.assertTrue(any("not enabled" in warning for warning in result.warnings))
        self.assertTrue(any("will not subscribe" in warning for warning in result.warnings))
        self.assertTrue(any("never subscribes" in warning for warning in result.warnings))

    def test_bundled_translation_ignores_workshop_requirement(self) -> None:
        items = [
            evidence(
                "a",
                description="This translation includes the original mod content.",
                workshop_id="100",
                required_workshop_items=("200",),
            ),
            evidence("b", workshop_id="200"),
        ]
        result = evaluate_order(items, ["a", "b"])
        self.assertEqual(result.recommended_order, ["a", "b"])
        self.assertTrue(any("bundled" in warning for warning in result.warnings))
        self.assertFalse(
            any(
                relation.source == "workshop-requirement"
                for relation in result.relations
            )
        )

    def test_already_correct_order_is_unchanged(self) -> None:
        items = [
            evidence("b", workshop_id="200"),
            evidence("a", workshop_id="100", required_workshop_items=("200",)),
        ]
        result = evaluate_order(items, ["b", "a"])
        self.assertEqual(result.recommended_order, ["b", "a"])


class CategoryTests(unittest.TestCase):
    def test_framework_before_content(self) -> None:
        items = [
            evidence("content", db_tables=("db/units_tables",)),
            evidence("framework", name="Core Framework"),
        ]
        result = evaluate_order(items, ["content", "framework"])
        self.assertEqual(result.recommended_order, ["framework", "content"])

    def test_language_after_content(self) -> None:
        items = [
            evidence("language", name="Chinese Translation", localization_paths=("localisation/zh.pack",)),
            evidence("content", db_tables=("db/units_tables",)),
        ]
        result = evaluate_order(items, ["language", "content"])
        self.assertEqual(result.recommended_order, ["content", "language"])

    def test_patch_after_content_when_categories_differ(self) -> None:
        items = [
            evidence("patch", name="Compatibility Patch", db_tables=("db/units_tables",)),
            evidence("content", db_tables=("db/units_tables",)),
        ]
        result = evaluate_order(items, ["patch", "content"])
        self.assertEqual(result.recommended_order, ["content", "patch"])

    def test_category_relations_are_soft(self) -> None:
        items = [evidence("content", db_tables=("db/x",)), evidence("framework", name="Core")]
        relations = build_category_relations([item.with_category() for item in items])
        self.assertTrue(relations)
        self.assertTrue(all(relation.kind == SOFT for relation in relations))


class ConflictTests(unittest.TestCase):
    def test_shared_db_table_is_a_conflict(self) -> None:
        items = [
            evidence("a", db_tables=("db/units_tables",)),
            evidence("b", db_tables=("db/units_tables",)),
        ]
        conflicts = detect_conflicts(items)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].kind, "db_table")
        self.assertEqual(conflicts[0].severity, "high")

    def test_shared_script_is_a_conflict(self) -> None:
        items = [
            evidence("a", script_paths=("script/campaign/mod/x.lua",)),
            evidence("b", script_paths=("script/campaign/mod/x.lua",)),
        ]
        conflicts = detect_conflicts(items)
        self.assertEqual(conflicts[0].kind, "script")

    def test_no_conflict_for_distinct_resources(self) -> None:
        items = [
            evidence("a", db_tables=("db/a",)),
            evidence("b", db_tables=("db/b",)),
        ]
        self.assertEqual(detect_conflicts(items), [])


class TopologicalTests(unittest.TestCase):
    def test_cycle_drops_the_lowest_confidence_relation(self) -> None:
        relations = [
            OrderRelation("a", "b", HARD, "a before b", 1.0),
            OrderRelation("b", "a", SOFT, "b before a", 0.6),
        ]
        order, dropped, warnings = stable_topological_order(["a", "b"], relations)
        self.assertEqual(order, ["a", "b"])
        self.assertEqual(len(dropped), 1)
        self.assertTrue(warnings)

    def test_unrelated_mods_keep_relative_order(self) -> None:
        order, _, _ = stable_topological_order(["c", "a", "b"], [])
        self.assertEqual(order, ["c", "a", "b"])

    def test_items_report_current_and_suggested_indexes(self) -> None:
        items = [
            evidence("a", workshop_id="100", required_workshop_items=("200",)),
            evidence("b", workshop_id="200"),
        ]
        result = evaluate_order(items, ["a", "b"])
        payload = {item["mod_id"]: item for item in result.items}
        self.assertEqual(payload["a"]["current_index"], 0)
        self.assertEqual(payload["a"]["suggested_index"], 1)
        self.assertEqual(payload["a"]["move"], 1)
        self.assertTrue(payload["a"]["reasons"])

    def test_baseline_order_changes_the_starting_point_not_the_reported_current_index(self) -> None:
        items = [evidence("a"), evidence("b")]
        result = evaluate_order(
            items,
            ["b", "a"],
            baseline_order=["a", "b"],
        )
        self.assertEqual(result.recommended_order, ["a", "b"])
        payload = {item["mod_id"]: item for item in result.items}
        self.assertEqual(payload["a"]["current_index"], 1)
        self.assertEqual(payload["a"]["suggested_index"], 0)


if __name__ == "__main__":
    unittest.main()
