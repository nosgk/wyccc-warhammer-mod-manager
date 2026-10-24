"""Background MOD order analysis.

The service collects evidence, evaluates the deterministic rules, optionally
asks the configured AI for extra relations, and exposes a polling state machine
for the desktop bridge. It never applies the result by itself.
"""

from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path
from typing import Any

from .ai_service import analyze_mod_order_relations, validate_ai_settings
from .constants import WH3_APP_ID
from .models import ModAsset
from .mod_ai_analysis_cache import ModAIAnalysisCache, analysis_cache_key
from .mod_order_evidence import EvidenceCollection, collect_evidence
from .mod_order_rules import (
    HARD,
    SOFT,
    ModOrderEvidence,
    OrderConflict,
    OrderRelation,
    evaluate_order,
)
from .workshop_comments import AnalysisCancelled, WorkshopCommentsClient, check_cancelled
from .workshop import WorkshopMetadataService

AI_BATCH_SIZE = 10


class ModOrderAnalysisService:
    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        comments_client: WorkshopCommentsClient | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._closed = False
        self._state: dict[str, Any] = self._idle_state()
        self.cache = ModAIAnalysisCache(cache_dir)
        self.comments_client = comments_client or WorkshopCommentsClient()

    @staticmethod
    def _idle_state() -> dict[str, Any]:
        return {
            "id": "",
            "status": "idle",
            "running": False,
            "phase": "idle",
            "progress": 0,
            "total_mods": 0,
            "completed_mods": 0,
            "current_mod": "",
            "ai_enabled": False,
            "degraded": False,
            "warnings": [],
            "error": "",
            "result": None,
            "cache_bytes": 0,
            "cache_limit": 0,
        }

    def status(self, session_id: str = "") -> dict[str, Any]:
        with self._lock:
            if session_id and self._state.get("id") != session_id:
                raise ValueError("aiOrder.staleSession")
            return copy.deepcopy(self._state)

    def cancel(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            if self._state.get("id") != session_id:
                raise ValueError("aiOrder.staleSession")
            if self._state.get("running"):
                self._cancel.set()
                self._state["phase"] = "cancelling"
            return copy.deepcopy(self._state)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()

    def apply(self, session_id: str) -> list[str]:
        with self._lock:
            if self._state.get("id") != session_id:
                raise ValueError("aiOrder.staleSession")
            result = self._state.get("result")
            if self._state.get("status") != "ready" or not isinstance(result, dict):
                raise ValueError("aiOrder.notReady")
            order = result.get("recommended_order")
            if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
                raise ValueError("aiOrder.invalidResult")
            return list(order)

    def start(
        self,
        assets: list[ModAsset],
        settings: dict[str, Any],
        *,
        game_id: str,
        app_id: int | str = WH3_APP_ID,
        current_order: list[str] | None = None,
        expected_token: str = "",
        workshop_service: WorkshopMetadataService | None = None,
    ) -> dict[str, Any]:
        if not assets:
            raise ValueError("aiOrder.noMods")
        ai_enabled = bool(settings.get("ai_enabled"))
        if ai_enabled:
            validate_ai_settings(settings)
        with self._lock:
            if self._closed or self._state.get("running"):
                raise ValueError("aiOrder.alreadyRunning")
            self._cancel = threading.Event()
            self._state = {
                **self._idle_state(),
                "id": uuid.uuid4().hex,
                "status": "running",
                "running": True,
                "phase": "collecting",
                "total_mods": len(assets),
                "ai_enabled": ai_enabled,
                "cache_limit": self.cache.max_bytes,
                "game_id": game_id,
                "expected_token": expected_token,
            }
            initial = copy.deepcopy(self._state)
            threading.Thread(
                target=self._run,
                args=(
                    copy.deepcopy(assets),
                    copy.deepcopy(settings),
                    list(current_order or []),
                    game_id,
                    int(app_id),
                    workshop_service,
                    self._cancel,
                ),
                name="mod-order-analysis",
                daemon=True,
            ).start()
            return initial

    def _update(self, **values: Any) -> None:
        with self._lock:
            if self._cancel.is_set() and values.get("running", True):
                values.pop("phase", None)
            self._state.update(values)

    def _report(self, phase: str, completed: int, total: int) -> None:
        self._update(
            phase=phase,
            completed_mods=completed,
            progress=int(40 * completed / max(1, total)),
        )

    def _run(
        self,
        assets: list[ModAsset],
        settings: dict[str, Any],
        current_order: list[str],
        game_id: str,
        app_id: int,
        workshop_service: WorkshopMetadataService | None,
        cancel: threading.Event,
    ) -> None:
        try:
            collection = collect_evidence(
                assets,
                workshop_service=workshop_service,
                comments_client=self.comments_client,
                app_id=app_id,
                language=str(settings.get("language") or "en-US"),
                include_comments=bool(settings.get("ai_enabled")),
                cancel=cancel,
                report=self._report,
            )
            check_cancelled(cancel)
            self._update(
                phase="evaluating",
                progress=50,
                warnings=list(collection.warnings),
                cache_bytes=self.cache.size_bytes,
            )
            base = evaluate_order(collection.items, current_order)
            ai_relations: list[OrderRelation] = []
            ai_conflicts: list[OrderConflict] = []
            degraded = False
            if settings.get("ai_enabled"):
                try:
                    ai_relations, ai_conflicts = self._analyze_relations(
                        collection, settings, cancel
                    )
                except AnalysisCancelled:
                    raise
                except Exception as exc:  # AI failure must not discard the deterministic result
                    degraded = True
                    collection.warnings.append(f"AI ordering unavailable: {exc}")
            check_cancelled(cancel)
            if settings.get("ai_enabled"):
                result = evaluate_order(
                    collection.items,
                    current_order,
                    ai_relations=ai_relations,
                )
            else:
                result = base
            result.warnings.extend(collection.warnings)
            result.conflicts.extend(ai_conflicts)
            self._update(
                status="ready",
                running=False,
                phase="done",
                progress=100,
                completed_mods=len(collection.items),
                current_mod="",
                degraded=degraded,
                warnings=list(result.warnings),
                result=result.to_dict(),
                cache_bytes=self.cache.size_bytes,
            )
        except AnalysisCancelled:
            self._update(status="cancelled", running=False, phase="cancelled", current_mod="")
        except Exception as exc:
            self._update(
                status="error",
                running=False,
                phase="error",
                error=str(exc) or exc.__class__.__name__,
                current_mod="",
            )

    def _analyze_relations(
        self,
        collection: EvidenceCollection,
        settings: dict[str, Any],
        cancel: threading.Event,
    ) -> tuple[list[OrderRelation], list[OrderConflict]]:
        items = [item.with_category() for item in collection.items]
        batches = [items[index : index + AI_BATCH_SIZE] for index in range(0, len(items), AI_BATCH_SIZE)]
        relations: list[OrderRelation] = []
        conflicts: list[OrderConflict] = []
        for batch_index, batch in enumerate(batches):
            check_cancelled(cancel)
            self._update(
                phase="analyzing",
                current_mod=batch[0].label if batch else "",
                progress=50 + int(45 * batch_index / max(1, len(batches))),
            )
            payload = [self._evidence_payload(item) for item in batch]
            key = analysis_cache_key(
                "order-relations",
                settings,
                {"mods": payload, "version": 1},
            )
            cached = self.cache.get(key)
            if isinstance(cached, list):
                parsed = cached
            else:
                parsed = analyze_mod_order_relations(payload, settings)
                self.cache.put(key, parsed)
            for relation in parsed:
                if not isinstance(relation, dict):
                    continue
                before = str(relation.get("before") or "")
                after = str(relation.get("after") or "")
                kind = str(relation.get("relation") or "")
                reason = str(relation.get("reason") or "").strip()
                try:
                    confidence = float(relation.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    continue
                if not before or not after or before == after:
                    continue
                if kind == "conflict":
                    conflicts.append(
                        OrderConflict(
                            mods=(before, after),
                            resource=reason or "ai-detected conflict",
                            kind="ai",
                            severity="medium",
                        )
                    )
                    continue
                if kind == "must_before":
                    relations.append(OrderRelation(before, after, HARD, reason, confidence, "ai"))
                elif kind == "should_before":
                    relations.append(OrderRelation(before, after, SOFT, reason, confidence, "ai"))
        return relations, conflicts

    @staticmethod
    def _evidence_payload(item: ModOrderEvidence) -> dict[str, Any]:
        return {
            "id": item.mod_id,
            "name": item.label,
            "pack_name": item.pack_name,
            "category": item.category,
            "description": item.description[:800],
            "requires": list(item.required_workshop_items),
            "dependencies": list(item.dependencies),
            "db_tables": list(item.db_tables[:20]),
            "scripts": list(item.script_paths[:10]),
            "localization": bool(item.localization_paths),
            "variant_selector": item.provides_variant_selector,
            "comments": list(item.comments[:5]),
        }
