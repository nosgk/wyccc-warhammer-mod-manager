"""Collect local and Workshop evidence for MOD order evaluation.

Evidence collection is read-only. Network access is limited to Workshop
metadata/comments and every failure is downgraded to a warning so the
deterministic ordering rules can still run offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable

from .constants import WH3_APP_ID
from .models import ModAsset
from .mod_order_rules import ModOrderEvidence
from .pack_reader import read_pack_index
from .workshop_comments import AnalysisCancelled, WorkshopCommentsClient, check_cancelled
from .workshop import WorkshopMetadataService

_ORDER_HINT_RE = re.compile(
    r"(load|order|before|after|crash|incompatible|compatib|patch|overwrite|conflict|"
    r"加载|排序|前置|崩溃|不兼容|兼容|补丁|覆盖|冲突)",
    re.IGNORECASE,
)


@dataclass
class EvidenceCollection:
    items: list[ModOrderEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    comments_fetched: int = 0
    metadata_fetched: int = 0


def _required_workshop_ids(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            candidate = str(value.get("workshop_id") or value.get("id") or "").strip()
        else:
            candidate = str(value or "").strip()
        if candidate.isdigit():
            result.append(candidate)
    return tuple(dict.fromkeys(result))


def _pack_paths(asset: ModAsset) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    path = Path(asset.path)
    if not path.is_file():
        return (), (), (), (), (), ()
    try:
        entries = read_pack_index(path)
    except (OSError, ValueError):
        return (), (), (), (), (), ()
    names = [entry.name.replace("\\", "/").casefold() for entry in entries]
    db_tables = tuple(sorted({name for name in names if name.startswith("db/")}))
    scripts = tuple(sorted({name for name in names if name.startswith("script/")}))
    localization = tuple(
        sorted({name for name in names if name.startswith(("localisation/", "text/"))})
    )
    ui = tuple(sorted({name for name in names if name.startswith("ui/")}))
    variants = tuple(
        sorted(
            {
                name
                for name in names
                if name.startswith(("variantmeshes/", "materials/", "textures/"))
            }
        )
    )
    return tuple(names), db_tables, scripts, localization, ui, variants


def _comment_texts(comments: Iterable[dict[str, Any]], limit: int) -> tuple[str, ...]:
    selected: list[str] = []
    for comment in comments:
        text = str(comment.get("text") or "").strip()
        if not text or not _ORDER_HINT_RE.search(text):
            continue
        selected.append(text[:500])
        if len(selected) >= limit:
            break
    return tuple(selected)


def collect_evidence(
    assets: list[ModAsset],
    *,
    workshop_service: WorkshopMetadataService | None = None,
    comments_client: WorkshopCommentsClient | None = None,
    app_id: int | str = WH3_APP_ID,
    language: str = "en-US",
    include_comments: bool = False,
    max_comments: int = 20,
    cancel: Event | None = None,
    report: Callable[[str, int, int], None] | None = None,
) -> EvidenceCollection:
    collection = EvidenceCollection()
    workshop_ids = [asset.workshop_id for asset in assets if asset.workshop_id]
    metadata: dict[str, dict[str, Any]] = {}
    if workshop_service is not None and workshop_ids:
        try:
            metadata = workshop_service.get_many(
                list(dict.fromkeys(workshop_ids)), language, app_id=app_id
            )
            collection.metadata_fetched = len(metadata)
        except (OSError, ValueError) as exc:
            collection.warnings.append(f"Workshop metadata unavailable: {exc}")

    for index, asset in enumerate(assets):
        if cancel is not None:
            check_cancelled(cancel)
        if report is not None:
            report("collecting", index + 1, len(assets))

        entries, db_tables, scripts, localization, ui, variants = _pack_paths(asset)
        record = metadata.get(asset.workshop_id, {}) if asset.workshop_id else {}
        required = _required_workshop_ids(asset.required_workshop_items) or _required_workshop_ids(
            record.get("required_workshop_items")
        )
        dependencies = tuple(
            str(name).strip()
            for name in (asset.dependency_packs or [])
            if str(name).strip()
        )

        comments: tuple[str, ...] = ()
        if include_comments and comments_client is not None and asset.workshop_id:
            try:
                page = comments_client.recent_comments(
                    asset.workshop_id, int(app_id), cancel or Event(), lambda _count: None
                )
                comments = _comment_texts(page.get("comments", []), max_comments)
                collection.comments_fetched += len(comments)
            except AnalysisCancelled:
                raise
            except (OSError, ValueError) as exc:
                collection.warnings.append(
                    f"{asset.effective_name}: comments unavailable ({exc})"
                )

        collection.items.append(
            ModOrderEvidence(
                mod_id=asset.id,
                name=asset.effective_name,
                pack_name=asset.pack_name,
                workshop_id=asset.workshop_id,
                title=str(record.get("title") or asset.display_name or ""),
                description=str(record.get("description") or asset.description or ""),
                author=str(record.get("author") or asset.author or ""),
                source=asset.source,
                updated_at=int(asset.updated_at or 0),
                workshop_updated_at=int(record.get("updated_at") or asset.workshop_updated_at or 0),
                dependencies=dependencies,
                required_workshop_items=required,
                pack_type=asset.pack_type,
                entry_names=entries,
                db_tables=db_tables,
                script_paths=scripts,
                localization_paths=localization,
                ui_paths=ui,
                variant_paths=variants,
                unit_data_tables=tuple(asset.unit_data_tables or ()),
                provides_variant_selector=bool(asset.provides_variant_selector),
                comments=comments,
            )
        )

    return collection
