"""Deterministic MOD load-order evaluation.

This module is intentionally free of filesystem and network access. It takes
evidence collected elsewhere and returns a recommended order, the relations
that produced it, and resource conflicts. The AI layer can add relations, but
the deterministic result must always stand on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

HARD = "hard"
SOFT = "soft"

CATEGORY_PRIORITY = {
    "framework": 10,
    "overhaul": 20,
    "content": 30,
    "visual": 40,
    "ui": 50,
    "variant": 60,
    "patch": 80,
    "language": 90,
    "other": 50,
}

_LANGUAGE_HINTS = ("translation", "localization", "localisation", "汉化", "翻译", "语言包", "language pack")
_FRAMEWORK_HINTS = ("framework", "library", "core", "base", "framework mod", "框架", "基础")
_OVERHAUL_HINTS = ("overhaul", "大修", "重做", "全面重制")
_PATCH_HINTS = ("patch", "compat", "compatibility", "fix", "兼容", "修复", "补丁")
_BUNDLE_HINTS = (
    "包含本体",
    "包含原版",
    "包含原 mod",
    "包含原mod",
    "已包含原",
    "自带本体",
    "无需原版",
    "不需要原版",
    "includes the original",
    "includes original",
    "bundles the original",
    "standalone",
    "no original mod required",
    "does not require the original",
)


def description_bundles_original(description: str) -> bool:
    """Detect descriptions that say the translation already contains the original.

    Some translation MODs bundle the original content because the source text is
    hardcoded. In that case a Workshop "required item" pointing at the original
    MOD is not a real load-order dependency.
    """
    text = str(description or "").casefold()
    return any(hint in text for hint in _BUNDLE_HINTS)


@dataclass(frozen=True)
class ModOrderEvidence:
    mod_id: str
    name: str
    pack_name: str
    workshop_id: str = ""
    title: str = ""
    description: str = ""
    author: str = ""
    source: str = ""
    updated_at: int = 0
    workshop_updated_at: int = 0
    dependencies: tuple[str, ...] = ()
    required_workshop_items: tuple[str, ...] = ()
    pack_type: str = "unknown"
    entry_names: tuple[str, ...] = ()
    db_tables: tuple[str, ...] = ()
    script_paths: tuple[str, ...] = ()
    localization_paths: tuple[str, ...] = ()
    ui_paths: tuple[str, ...] = ()
    variant_paths: tuple[str, ...] = ()
    unit_data_tables: tuple[str, ...] = ()
    provides_variant_selector: bool = False
    comments: tuple[str, ...] = ()
    category: str = ""

    @property
    def label(self) -> str:
        return self.name or self.title or self.pack_name or self.mod_id

    def with_category(self) -> "ModOrderEvidence":
        if self.category:
            return self
        return ModOrderEvidence(**{**self.__dict__, "category": classify_evidence(self)})


@dataclass(frozen=True)
class OrderRelation:
    before: str
    after: str
    kind: str
    reason: str
    confidence: float = 1.0
    source: str = "rule"


@dataclass(frozen=True)
class OrderConflict:
    mods: tuple[str, ...]
    resource: str
    kind: str
    severity: str


@dataclass
class OrderEvaluation:
    recommended_order: list[str]
    relations: list[OrderRelation]
    conflicts: list[OrderConflict]
    warnings: list[str]
    dropped_relations: list[OrderRelation] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "recommended_order": list(self.recommended_order),
            "relations": [
                {
                    "before": relation.before,
                    "after": relation.after,
                    "kind": relation.kind,
                    "reason": relation.reason,
                    "confidence": relation.confidence,
                    "source": relation.source,
                }
                for relation in self.relations
            ],
            "conflicts": [
                {
                    "mods": list(conflict.mods),
                    "resource": conflict.resource,
                    "kind": conflict.kind,
                    "severity": conflict.severity,
                }
                for conflict in self.conflicts
            ],
            "warnings": list(self.warnings),
            "dropped_relations": [
                {
                    "before": relation.before,
                    "after": relation.after,
                    "kind": relation.kind,
                    "reason": relation.reason,
                    "confidence": relation.confidence,
                    "source": relation.source,
                }
                for relation in self.dropped_relations
            ],
            "items": list(self.items),
        }


def _text(evidence: ModOrderEvidence) -> str:
    return " ".join(
        part for part in (evidence.name, evidence.title, evidence.pack_name, evidence.description) if part
    ).casefold()


def classify_evidence(evidence: ModOrderEvidence) -> str:
    text = _text(evidence)
    if evidence.localization_paths or any(hint in text for hint in _LANGUAGE_HINTS):
        return "language"
    if any(hint in text for hint in _OVERHAUL_HINTS):
        return "overhaul"
    if any(hint in text for hint in _FRAMEWORK_HINTS):
        return "framework"
    if evidence.provides_variant_selector or evidence.variant_paths:
        return "variant"
    if any(hint in text for hint in _PATCH_HINTS):
        return "patch"
    if evidence.db_tables or evidence.unit_data_tables:
        return "content"
    if evidence.ui_paths:
        return "ui"
    return "other"


def build_dependency_relations(
    evidence: Iterable[ModOrderEvidence],
    *,
    by_workshop: dict[str, list[str]],
    by_pack: dict[str, list[str]],
) -> tuple[list[OrderRelation], list[str]]:
    relations: list[OrderRelation] = []
    warnings: list[str] = []
    items = list(evidence)
    required_evidence = False
    for item in items:
        if item.required_workshop_items:
            required_evidence = True
        if item.required_workshop_items and description_bundles_original(item.description):
            warnings.append(
                f"{item.label}: description indicates the original content is bundled; "
                "Workshop required items are not treated as dependencies"
            )
        else:
            for workshop_id in item.required_workshop_items:
                candidates = [
                    mod_id for mod_id in by_workshop.get(workshop_id, []) if mod_id != item.mod_id
                ]
                if not candidates:
                    warnings.append(
                        f"{item.label}: Workshop required item {workshop_id} is not enabled; "
                        "the analysis will not subscribe it"
                    )
                    continue
                for candidate in candidates:
                    # Workshop required items are evidence, not commands: translation
                    # collections may bundle the original MOD. Keep the relation soft.
                    relations.append(
                        OrderRelation(
                            before=candidate,
                            after=item.mod_id,
                            kind=SOFT,
                            reason=(
                                f"Workshop required item {workshop_id} "
                                "(verify translation bundle; never auto-subscribed)"
                            ),
                            confidence=0.55,
                            source="workshop-requirement",
                        )
                    )
        for pack_name in item.dependencies:
            candidates = [
                mod_id for mod_id in by_pack.get(pack_name.casefold(), []) if mod_id != item.mod_id
            ]
            if not candidates:
                warnings.append(f"{item.label} requires missing Pack {pack_name}")
                continue
            for candidate in candidates:
                relations.append(
                    OrderRelation(
                        before=candidate,
                        after=item.mod_id,
                        kind=HARD,
                        reason=f"requires Pack {pack_name}",
                        confidence=1.0,
                        source="dependency",
                    )
                )
    if required_evidence:
        warnings.append(
            "Workshop required items are evidence only; the analysis never subscribes "
            "or enables extra MODs."
        )
    return relations, warnings


def build_category_relations(evidence: Iterable[ModOrderEvidence]) -> list[OrderRelation]:
    items = list(evidence)
    relations: list[OrderRelation] = []
    for left in items:
        for right in items:
            if left.mod_id == right.mod_id:
                continue
            left_priority = CATEGORY_PRIORITY.get(left.category, CATEGORY_PRIORITY["other"])
            right_priority = CATEGORY_PRIORITY.get(right.category, CATEGORY_PRIORITY["other"])
            if left_priority >= right_priority:
                continue
            # Only bridge the categories that actually depend on load order.
            if not _category_pair_matters(left.category, right.category):
                continue
            relations.append(
                OrderRelation(
                    before=left.mod_id,
                    after=right.mod_id,
                    kind=SOFT,
                    reason=f"{left.category} before {right.category}",
                    confidence=0.6,
                    source="category",
                )
            )
    return relations


def _category_pair_matters(before: str, after: str) -> bool:
    pairs = {
        ("framework", "content"),
        ("framework", "visual"),
        ("framework", "ui"),
        ("framework", "variant"),
        ("framework", "patch"),
        ("framework", "language"),
        ("overhaul", "content"),
        ("overhaul", "visual"),
        ("overhaul", "ui"),
        ("overhaul", "variant"),
        ("overhaul", "patch"),
        ("overhaul", "language"),
        ("content", "language"),
        ("visual", "language"),
        ("ui", "language"),
        ("variant", "language"),
        ("content", "patch"),
        ("visual", "patch"),
        ("ui", "patch"),
        ("variant", "patch"),
    }
    return (before, after) in pairs


def detect_conflicts(evidence: Iterable[ModOrderEvidence]) -> list[OrderConflict]:
    items = list(evidence)
    conflicts: list[OrderConflict] = []
    for attribute, kind, severity in (
        ("db_tables", "db_table", "high"),
        ("script_paths", "script", "high"),
        ("localization_paths", "localization", "medium"),
        ("ui_paths", "ui", "low"),
        ("variant_paths", "variant", "low"),
    ):
        owners: dict[str, list[str]] = {}
        for item in items:
            for resource in getattr(item, attribute):
                owners.setdefault(resource.casefold(), []).append(item.mod_id)
        for resource, mod_ids in sorted(owners.items()):
            unique = list(dict.fromkeys(mod_ids))
            if len(unique) < 2:
                continue
            conflicts.append(
                OrderConflict(
                    mods=tuple(unique),
                    resource=resource,
                    kind=kind,
                    severity=severity if len(unique) == 2 else "high",
                )
            )
    return conflicts


def merge_relations(*groups: Iterable[OrderRelation]) -> list[OrderRelation]:
    merged: dict[tuple[str, str], OrderRelation] = {}
    for group in groups:
        for relation in group:
            if not relation.before or not relation.after or relation.before == relation.after:
                continue
            key = (relation.before, relation.after)
            current = merged.get(key)
            if current is None or _relation_rank(relation) > _relation_rank(current):
                merged[key] = relation
    return list(merged.values())


def _relation_rank(relation: OrderRelation) -> tuple[int, float]:
    return (1 if relation.kind == HARD else 0, relation.confidence)


def stable_topological_order(
    mod_ids: list[str],
    relations: list[OrderRelation],
    *,
    pack_names: dict[str, str] | None = None,
) -> tuple[list[str], list[OrderRelation], list[str]]:
    """Order MODs with the fewest moves while satisfying hard relations.

    Kahn's algorithm picks the ready MOD that appears earliest in the user's
    current order. Unconstrained MODs therefore keep their relative order.
    """
    pack_names = pack_names or {}
    current_index = {mod_id: index for index, mod_id in enumerate(mod_ids)}
    active: dict[tuple[str, str], OrderRelation] = {}
    for relation in relations:
        if relation.before in current_index and relation.after in current_index:
            active[(relation.before, relation.after)] = relation

    dropped: list[OrderRelation] = []
    warnings: list[str] = []

    while True:
        outgoing: dict[str, set[str]] = {mod_id: set() for mod_id in mod_ids}
        indegree: dict[str, int] = {mod_id: 0 for mod_id in mod_ids}
        for relation in active.values():
            if relation.after not in outgoing[relation.before]:
                outgoing[relation.before].add(relation.after)
                indegree[relation.after] += 1

        ready = [mod_id for mod_id in mod_ids if indegree[mod_id] == 0]
        order: list[str] = []
        while ready:
            ready.sort(key=lambda mod_id: (current_index[mod_id], pack_names.get(mod_id, ""), mod_id))
            current = ready.pop(0)
            order.append(current)
            for target in sorted(outgoing[current], key=lambda mod_id: current_index[mod_id]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)

        if len(order) == len(mod_ids):
            return order, dropped, warnings

        cycle_ids = [mod_id for mod_id in mod_ids if mod_id not in order]
        cycle_relations = [
            relation
            for relation in active.values()
            if relation.before in cycle_ids and relation.after in cycle_ids
        ]
        if not cycle_relations:
            warnings.append("cycle detected without a removable relation; keeping current order")
            return mod_ids, dropped, warnings
        removable = min(cycle_relations, key=_relation_rank)
        active.pop((removable.before, removable.after), None)
        dropped.append(removable)
        warnings.append(
            f"dropped {removable.kind} relation {removable.before} -> {removable.after}: {removable.reason}"
        )


def evaluate_order(
    evidence: Iterable[ModOrderEvidence],
    current_order: list[str],
    *,
    ai_relations: Iterable[OrderRelation] = (),
    max_soft_confidence: float = 0.55,
) -> OrderEvaluation:
    items = [item.with_category() for item in evidence]
    by_id = {item.mod_id: item for item in items}
    ordered_ids = [mod_id for mod_id in current_order if mod_id in by_id]
    ordered_ids.extend(item.mod_id for item in items if item.mod_id not in ordered_ids)

    by_workshop: dict[str, list[str]] = {}
    by_pack: dict[str, list[str]] = {}
    for item in items:
        if item.workshop_id:
            by_workshop.setdefault(item.workshop_id, []).append(item.mod_id)
        by_pack.setdefault(item.pack_name.casefold(), []).append(item.mod_id)

    dependency_relations, warnings = build_dependency_relations(
        items, by_workshop=by_workshop, by_pack=by_pack
    )
    category_relations = [
        relation for relation in build_category_relations(items) if relation.confidence >= max_soft_confidence
    ]
    ai = [relation for relation in ai_relations if relation.before in by_id and relation.after in by_id]
    relations = merge_relations(dependency_relations, category_relations, ai)

    pack_names = {item.mod_id: item.pack_name.casefold() for item in items}
    recommended, dropped, order_warnings = stable_topological_order(
        ordered_ids, relations, pack_names=pack_names
    )
    warnings.extend(order_warnings)
    conflicts = detect_conflicts(items)

    item_payloads = _build_item_payloads(
        items,
        recommended,
        current_order=ordered_ids,
        relations=relations,
        conflicts=conflicts,
        by_id=by_id,
    )
    return OrderEvaluation(
        recommended_order=recommended,
        relations=relations,
        conflicts=conflicts,
        warnings=warnings,
        dropped_relations=dropped,
        items=item_payloads,
    )


def _build_item_payloads(
    items: list[ModOrderEvidence],
    recommended: list[str],
    *,
    current_order: list[str],
    relations: list[OrderRelation],
    conflicts: list[OrderConflict],
    by_id: dict[str, ModOrderEvidence],
) -> list[dict]:
    recommended_index = {mod_id: index for index, mod_id in enumerate(recommended)}
    current_index = {mod_id: index for index, mod_id in enumerate(current_order)}
    payloads: list[dict] = []
    for item in items:
        reasons: list[str] = []
        constraints: list[dict] = []
        warnings: list[str] = []
        confidence = 0.5
        for relation in relations:
            if relation.after == item.mod_id:
                reasons.append(f"after {by_id[relation.before].label}: {relation.reason}")
                constraints.append(
                    {
                        "type": relation.kind,
                        "direction": "after",
                        "target": relation.before,
                        "reason": relation.reason,
                    }
                )
                confidence = max(confidence, relation.confidence)
            elif relation.before == item.mod_id:
                reasons.append(f"before {by_id[relation.after].label}: {relation.reason}")
                constraints.append(
                    {
                        "type": relation.kind,
                        "direction": "before",
                        "target": relation.after,
                        "reason": relation.reason,
                    }
                )
                confidence = max(confidence, relation.confidence)
        for conflict in conflicts:
            if item.mod_id in conflict.mods:
                others = [by_id[mod_id].label for mod_id in conflict.mods if mod_id != item.mod_id]
                warnings.append(f"shares {conflict.kind} {conflict.resource} with {', '.join(others)}")
        current = current_index.get(item.mod_id)
        suggested = recommended_index[item.mod_id]
        payloads.append(
            {
                "mod_id": item.mod_id,
                "name": item.label,
                "pack_name": item.pack_name,
                "category": item.category,
                "current_index": current,
                "suggested_index": suggested,
                "move": (suggested - current) if current is not None else None,
                "confidence": round(confidence, 2),
                "reasons": reasons,
                "constraints": constraints,
                "warnings": warnings,
            }
        )
    payloads.sort(key=lambda payload: payload["suggested_index"])
    return payloads
