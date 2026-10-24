"""Background MOD order analysis.

The service collects evidence, evaluates the deterministic rules, optionally
asks the configured AI for extra relations, and exposes a polling state machine
for the desktop bridge. It never applies the result by itself.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .ai_service import analyze_mod_order_relations, validate_ai_settings
from .constants import WH3_APP_ID
from .models import ModAsset
from .mod_ai_analysis_cache import ModAIAnalysisCache, analysis_cache_key
from .mod_order_evidence import EvidenceCollection, collect_evidence
from .mod_order_history import ModOrderHistoryStore
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
AI_GLOBAL_BATCH_SIZE = 40
AI_GLOBAL_THRESHOLD = 20
AI_RETRIES = 10
MAX_EVENTS = 200
MAX_ANCHORS = 20
ANCHOR_CATEGORIES = {"framework", "overhaul", "patch", "language"}


class ModOrderAnalysisService:
    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        history_path: Path | None = None,
        comments_client: WorkshopCommentsClient | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._closed = False
        self._state: dict[str, Any] = self._idle_state()
        self.cache = ModAIAnalysisCache(cache_dir)
        self.history = ModOrderHistoryStore(history_path)
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
            "baseline": "current",
            "warnings": [],
            "error": "",
            "result": None,
            "events": [],
            "stats": {
                "metadata_fetched": 0,
                "comments_fetched": 0,
                "relations_total": 0,
                "relations_dependency": 0,
                "relations_category": 0,
                "relations_workshop": 0,
                "relations_ai": 0,
                "conflicts": 0,
                "dropped_relations": 0,
                "ai_local_batches": 0,
                "ai_global_batches": 0,
                "ai_retries": 0,
                "cache_hits": 0,
            },
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
                self._event("cancelling", "已请求取消，等待当前请求返回")
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

    def list_history(self) -> list[dict[str, Any]]:
        return self.history.list()

    def get_history(self, record_id: str) -> dict[str, Any]:
        return self.history.get(record_id)

    def apply_history(self, record_id: str) -> list[str]:
        record = self.history.get(record_id)
        order = record.get("recommended_order")
        if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
            raise ValueError("aiOrder.invalidResult")
        return list(order)

    def mark_applied(self, record_id: str, ordered_mod_ids: list[str]) -> dict[str, Any]:
        return self.history.mark_applied(record_id, ordered_mod_ids)

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
        baseline: str = "current",
    ) -> dict[str, Any]:
        if not assets:
            raise ValueError("aiOrder.noMods")
        if baseline not in {"current", "default"}:
            raise ValueError("aiOrder.invalidBaseline")
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
                "baseline": baseline,
                "cache_limit": self.cache.max_bytes,
                "game_id": game_id,
                "expected_token": expected_token,
            }
            self._event("collecting", f"开始分析 {len(assets)} 个 MOD")
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
                    baseline,
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

    def _event(self, phase: str, message: str, **data: Any) -> None:
        with self._lock:
            events = list(self._state.get("events") or [])
            events.append(
                {
                    "at": int(time.time() * 1000),
                    "phase": phase,
                    "message": message,
                    **data,
                }
            )
            self._state["events"] = events[-MAX_EVENTS:]

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._lock:
            stats = dict(self._state.get("stats") or {})
            stats[key] = int(stats.get(key) or 0) + amount
            self._state["stats"] = stats

    def _report(self, phase: str, completed: int, total: int) -> None:
        self._update(
            phase=phase,
            completed_mods=completed,
            progress=int(40 * completed / max(1, total)),
        )

    @staticmethod
    def _default_order(assets: list[ModAsset]) -> list[str]:
        ordered = sorted(
            assets,
            key=lambda asset: (asset.pack_name.casefold(), asset.id.casefold()),
        )
        return [asset.id for asset in ordered]

    def _run(
        self,
        assets: list[ModAsset],
        settings: dict[str, Any],
        current_order: list[str],
        game_id: str,
        app_id: int,
        workshop_service: WorkshopMetadataService | None,
        baseline: str,
        cancel: threading.Event,
    ) -> None:
        started_at = int(time.time() * 1000)
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
            self._bump("metadata_fetched", collection.metadata_fetched)
            self._bump("comments_fetched", collection.comments_fetched)
            self._event(
                "collecting",
                f"证据收集完成：{len(collection.items)} 个 MOD",
                metadata=collection.metadata_fetched,
                comments=collection.comments_fetched,
            )
            self._update(
                phase="evaluating",
                progress=50,
                warnings=list(collection.warnings),
                cache_bytes=self.cache.size_bytes,
            )
            baseline_order = (
                self._default_order(assets) if baseline == "default" else list(current_order)
            )
            base = evaluate_order(
                collection.items,
                current_order,
                baseline_order=baseline_order,
            )
            self._event(
                "evaluating",
                "本地规则评估完成",
                relations=len(base.relations),
                conflicts=len(base.conflicts),
                baseline=baseline,
            )
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
                    self._event("degraded", f"AI 不可用，使用本地规则：{exc}")
            check_cancelled(cancel)
            if settings.get("ai_enabled"):
                result = evaluate_order(
                    collection.items,
                    current_order,
                    ai_relations=ai_relations,
                    baseline_order=baseline_order,
                )
            else:
                result = base
            result.warnings.extend(collection.warnings)
            result.conflicts.extend(ai_conflicts)
            self._record_stats(result)
            self._event(
                "done",
                "关系合并与排序完成",
                recommended=len(result.recommended_order),
                relations=len(result.relations),
                conflicts=len(result.conflicts),
            )
            record = {
                "id": self._state["id"],
                "created_at": started_at,
                "completed_at": int(time.time() * 1000),
                "game_id": game_id,
                "total_mods": len(collection.items),
                "ai_enabled": bool(settings.get("ai_enabled")),
                "degraded": degraded,
                "model": str(settings.get("ai_model") or ""),
                "baseline": baseline,
                "current_order": list(current_order),
                "recommended_order": list(result.recommended_order),
                "result": result.to_dict(),
                "warnings": list(result.warnings),
            }
            self.history.add(record)
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
            self._event("cancelled", "分析已取消")
            self._update(status="cancelled", running=False, phase="cancelled", current_mod="")
        except Exception as exc:
            self._event("error", f"分析失败：{exc}")
            self._update(
                status="error",
                running=False,
                phase="error",
                error=str(exc) or exc.__class__.__name__,
                current_mod="",
            )

    def _record_stats(self, result: Any) -> None:
        with self._lock:
            stats = dict(self._state.get("stats") or {})
            stats["relations_total"] = len(result.relations)
            source_keys = {
                "dependency": "relations_dependency",
                "category": "relations_category",
                "workshop-requirement": "relations_workshop",
                "ai": "relations_ai",
            }
            for relation in result.relations:
                key = source_keys.get(relation.source, f"relations_{relation.source}")
                stats[key] = int(stats.get(key) or 0) + 1
            stats["conflicts"] = len(result.conflicts)
            stats["dropped_relations"] = len(result.dropped_relations)
            self._state["stats"] = stats

    def _request_relations(
        self,
        items: list[ModOrderEvidence],
        settings: dict[str, Any],
        cancel: threading.Event,
        *,
        cache_kind: str,
    ) -> tuple[list[OrderRelation], list[OrderConflict]]:
        payload = [self._evidence_payload(item) for item in items]
        key = analysis_cache_key(
            cache_kind,
            settings,
            {"mods": payload, "version": 2},
        )
        cached = self.cache.get(key)
        if isinstance(cached, list):
            self._bump("cache_hits")
            parsed = cached
        else:
            def on_retry(attempt: int, exc: Exception) -> None:
                self._bump("ai_retries")
                self._event("retrying", f"AI 重试 {attempt}/{AI_RETRIES}：{exc}")

            parsed = analyze_mod_order_relations(
                payload,
                settings,
                retries=AI_RETRIES,
                on_retry=on_retry,
            )
            self.cache.put(key, parsed)
        return self._convert_relations(parsed)

    @staticmethod
    def _convert_relations(
        parsed: list[dict[str, Any]]
    ) -> tuple[list[OrderRelation], list[OrderConflict]]:
        relations: list[OrderRelation] = []
        conflicts: list[OrderConflict] = []
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

    def _analyze_relations(
        self,
        collection: EvidenceCollection,
        settings: dict[str, Any],
        cancel: threading.Event,
    ) -> tuple[list[OrderRelation], list[OrderConflict]]:
        items = [item.with_category() for item in collection.items]
        relations: list[OrderRelation] = []
        conflicts: list[OrderConflict] = []

        local_batches = [
            items[index : index + AI_BATCH_SIZE]
            for index in range(0, len(items), AI_BATCH_SIZE)
        ]
        for batch_index, batch in enumerate(local_batches):
            check_cancelled(cancel)
            self._update(
                phase="analyzing",
                current_mod=batch[0].label if batch else "",
                progress=50 + int(35 * batch_index / max(1, len(local_batches))),
            )
            self._event(
                "analyzing",
                f"AI 局部分析：第 {batch_index + 1}/{len(local_batches)} 批",
                count=len(batch),
            )
            batch_relations, batch_conflicts = self._request_relations(
                batch, settings, cancel, cache_kind="order-relations"
            )
            self._bump("ai_local_batches")
            relations.extend(batch_relations)
            conflicts.extend(batch_conflicts)

        if len(items) > AI_GLOBAL_THRESHOLD:
            anchors = [
                item for item in items if item.category in ANCHOR_CATEGORIES
            ][:MAX_ANCHORS]
            global_chunks = [
                items[index : index + AI_GLOBAL_BATCH_SIZE]
                for index in range(0, len(items), AI_GLOBAL_BATCH_SIZE)
            ]
            for chunk_index, chunk in enumerate(global_chunks):
                check_cancelled(cancel)
                self._update(
                    phase="global",
                    current_mod=f"全局关系检查 {chunk_index + 1}/{len(global_chunks)}",
                    progress=85 + int(10 * chunk_index / max(1, len(global_chunks))),
                )
                payload_items = list(
                    {item.mod_id: item for item in [*chunk, *anchors]}.values()
                )
                self._event(
                    "global",
                    f"AI 全局关系检查：第 {chunk_index + 1}/{len(global_chunks)} 批",
                    count=len(payload_items),
                    anchors=len(anchors),
                )
                chunk_relations, chunk_conflicts = self._request_relations(
                    payload_items,
                    settings,
                    cancel,
                    cache_kind="order-relations-global",
                )
                self._bump("ai_global_batches")
                relations.extend(chunk_relations)
                conflicts.extend(chunk_conflicts)

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
