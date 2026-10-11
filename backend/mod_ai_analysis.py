from __future__ import annotations

import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from .ai_service import analyze_mod_comment_batch, summarize_mod_feedback, validate_ai_settings
from .models import ModAsset
from .mod_ai_analysis_cache import ModAIAnalysisCache, analysis_cache_key
from .workshop_comments import AnalysisCancelled, WorkshopCommentsClient, check_cancelled


MAX_AI_WORKERS = 5


def analysis_batches(records: Iterable[dict[str, Any]], max_chars: int = 14000, max_count: int = 25):
    """Bound requests without omitting records or truncating long comments."""
    batch: list[dict[str, Any]] = []
    size = 0
    for record in records:
        length = len(json.dumps(record, ensure_ascii=False))
        if batch and (size + length > max_chars or len(batch) >= max_count):
            yield batch
            batch, size = [], 0
        batch.append(record)
        size += length
    if batch:
        yield batch


class ModAIAnalysisService:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._closed = False
        self._state: dict[str, Any] = {"status": "idle", "running": False, "results": []}
        self.client = WorkshopCommentsClient()
        self.cache = ModAIAnalysisCache(cache_dir)
        self._ai_slots = threading.BoundedSemaphore(MAX_AI_WORKERS)

    def status(self, session_id: str = "") -> dict[str, Any]:
        with self._lock:
            if session_id and self._state.get("id") != session_id:
                raise ValueError("aiAnalysis.staleSession")
            return copy.deepcopy(self._state)

    def cancel(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            if self._state.get("id") != session_id:
                raise ValueError("aiAnalysis.staleSession")
            if self._state.get("running"):
                self._cancel.set()
                self._state["phase"] = "cancelling"
            return copy.deepcopy(self._state)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()

    def start(
        self, assets: list[ModAsset], settings: dict[str, Any], game_id: str, app_id: int
    ) -> dict[str, Any]:
        try:
            validate_ai_settings(settings)
        except ValueError as exc:
            raise ValueError("aiAnalysis.configureAI") from exc
        if not assets:
            raise ValueError("aiAnalysis.noEnabledMods")
        with self._lock:
            if self._closed or self._state.get("running"):
                raise ValueError("aiAnalysis.alreadyRunning")
            self._cancel = threading.Event()
            self._state = {
                "id": uuid.uuid4().hex, "game_id": game_id,
                "language": str(settings.get("language") or "en-US"), "model": str(settings["ai_model"]),
                "status": "running", "running": True, "phase": "fetching", "progress": 0,
                "total_mods": len(assets), "completed_mods": 0, "current_mod": "",
                "fetched_comments": 0, "analyzed_comments": 0, "batch_current": 0, "batch_total": 0,
                "cached_comments": 0, "cached_summaries": 0, "cache_bytes": 0, "cache_limit": self.cache.max_bytes,
                "active_workers": 0, "max_workers": MAX_AI_WORKERS,
                "results": [], "error": "",
            }
            initial = copy.deepcopy(self._state)
            # Take private settings and asset snapshots; never expose credentials or mutate the MOD list.
            threading.Thread(
                target=self._run,
                args=(copy.deepcopy(assets), copy.deepcopy(settings), app_id, self._cancel),
                name="workshop-ai-analysis", daemon=True,
            ).start()
            return initial

    def _update(self, **values: Any) -> None:
        with self._lock:
            # A cancellation message remains visible while the current network request returns.
            if self._cancel.is_set() and values.get("running", True):
                values.pop("phase", None)
            self._state.update(values)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        code = str(exc)
        return code if code.startswith("aiAnalysis.") else "aiAnalysis.aiFailed"

    def _ai_request(self, function, *args, cancel: threading.Event):
        # Summaries and comment batches share the same limit, including requests from different MODs.
        while not self._ai_slots.acquire(timeout=0.1):
            check_cancelled(cancel)
        try:
            check_cancelled(cancel)
            with self._lock:
                self._state["active_workers"] = self._state.get("active_workers", 0) + 1
            try:
                return function(*args)
            finally:
                with self._lock:
                    self._state["active_workers"] -= 1
        finally:
            self._ai_slots.release()

    def _summarize(self, records: list[dict[str, Any]], settings: dict[str, Any], cancel: threading.Event) -> str:
        pending = records
        while pending:
            batches = list(analysis_batches(pending))
            merged = []
            for index, batch in enumerate(batches):
                check_cancelled(cancel)
                self._update(batch_current=index + 1, batch_total=len(batches))
                key = analysis_cache_key("summary", settings, batch)
                summary = self.cache.get(key)
                if isinstance(summary, str) and summary.strip():
                    with self._lock:
                        self._state["cached_summaries"] = self._state.get("cached_summaries", 0) + 1
                else:
                    summary = self._ai_request(summarize_mod_feedback, batch, settings, cancel=cancel)
                    self.cache.put(key, summary)
                merged.append({"summary": summary})
                check_cancelled(cancel)
            if len(merged) == 1:
                return merged[0]["summary"]
            if len(merged) >= len(pending):
                raise ValueError("aiAnalysis.summaryTooLong")
            pending = merged
        return ""

    def _analyze_batch(
        self, mod: dict[str, Any], comments: list[dict[str, Any]], settings: dict[str, Any], cancel: threading.Event
    ) -> list[dict[str, Any]]:
        parsed = {}
        missing = []
        keys = {}
        for comment in comments:
            check_cancelled(cancel)
            comment_id = str(comment["id"])
            key = analysis_cache_key("comment", settings, {"mod": mod, "comment": comment})
            keys[comment_id] = key
            cached = self.cache.get(key)
            if isinstance(cached, dict):
                category = cached.get("category")
                translation, summary = cached.get("translation"), cached.get("summary")
                if (
                    isinstance(category, str) and category in {"issue", "question", "positive", "other"}
                    and isinstance(translation, str) and translation.strip() and isinstance(summary, str)
                    and (category != "issue" or summary.strip())
                ):
                    parsed[comment_id] = {
                        **comment, "category": category, "translation": translation,
                        "is_issue": category == "issue", "summary": summary if category == "issue" else "",
                    }
                    continue
            missing.append(comment)
        hits = len(parsed)
        if missing:
            check_cancelled(cancel)
            translated = self._ai_request(analyze_mod_comment_batch, mod, missing, settings, cancel=cancel)
            for comment in translated:
                comment_id = str(comment["id"])
                parsed[comment_id] = comment
                self.cache.put(keys[comment_id], {
                    field: comment.get(field) for field in ("category", "translation", "summary")
                })
        check_cancelled(cancel)
        with self._lock:
            self._state["cached_comments"] += hits
        self._update(cache_bytes=self.cache.size_bytes)
        return [parsed[str(comment["id"])] for comment in comments]

    def _analyze_mod(self, group, settings, app_id, cancel, ai_pool, report_fetched, report_analyzed):
        asset = group[0]
        result = {
            "mod_ids": [item.id for item in group], "name": " / ".join(item.effective_name for item in group),
            "pack_names": [item.pack_name for item in group], "title": "",
            "workshop_id": asset.workshop_id,
            "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={asset.workshop_id}"
            if asset.workshop_id else "",
            "status": "skipped", "error": "", "updated_at": 0,
            "comment_count": 0, "analyzed_count": 0, "issue_count": 0,
            "summary": "", "comments": [],
        }
        batch_results = {}
        futures = {}

        def retain_reports():
            # Completion order cannot change the Workshop order of the evidence.
            result["comments"] = [comment for index in sorted(batch_results)
                                  for comment in batch_results[index] if comment["is_issue"]]
            result["issue_count"] = len(result["comments"])

        try:
            check_cancelled(cancel)
            self._update(phase="fetching", current_mod=result["name"], batch_current=0, batch_total=0)
            if not asset.workshop_id:
                return result
            page_data = self.client.recent_comments(asset.workshop_id, app_id, cancel, report_fetched)
            check_cancelled(cancel)
            comments = page_data["comments"]
            result.update(
                updated_at=page_data["updated_at"], comment_count=len(comments),
                fetched_at=page_data["fetched_at"], title=page_data["title"],
            )
            report_fetched(len(comments))
            batches = list(analysis_batches(comments))
            mod = {"name": result["name"], "title": page_data["title"], "updated_at": result["updated_at"],
                   "workshop_id": asset.workshop_id, "app_id": app_id}
            if batches:
                self._update(phase="analyzing", current_mod=result["name"], batch_current=0, batch_total=len(batches))
            for index, batch in enumerate(batches):
                check_cancelled(cancel)
                futures[ai_pool.submit(self._analyze_batch, mod, batch, settings, cancel)] = index
            batch_error = ""
            completed_batches = 0
            for future in as_completed(futures):
                check_cancelled(cancel)
                index = futures[future]
                completed_batches += 1
                self._update(
                    phase="analyzing", current_mod=result["name"], batch_current=completed_batches, batch_total=len(batches),
                )
                try:
                    translated = future.result()
                except AnalysisCancelled:
                    raise
                except Exception as exc:
                    batch_error = batch_error or self._error_code(exc)
                else:
                    batch_results[index] = translated
                    result["analyzed_count"] += len(translated)
                    report_analyzed(len(translated), len(batch_results) / len(batches))
            retain_reports()
            if batch_error:
                result.update(status="error", error=batch_error)
            else:
                result["status"] = "no_comments" if not comments else "issues" if result["issue_count"] else "clean"
                if result["issue_count"]:
                    self._update(phase="mod_summary", current_mod=result["name"])
                    result["summary"] = self._summarize([
                        {"mod": result["name"], "comment": comment["translation"],
                         "is_issue": comment["is_issue"], "issue": comment["summary"]}
                        for comment in result["comments"]
                    ], settings, cancel)
        except AnalysisCancelled:
            for future, index in futures.items():
                if index not in batch_results and future.done() and not future.cancelled():
                    try:
                        translated = future.result()
                    except Exception:
                        continue
                    batch_results[index] = translated
                    result["analyzed_count"] += len(translated)
                    report_analyzed(len(translated), len(batch_results) / len(futures))
            retain_reports()
            result.update(status="cancelled")
        except Exception as exc:
            retain_reports()
            result.update(status="error", error=self._error_code(exc))
        return result

    def _run(self, assets: list[ModAsset], settings: dict[str, Any], app_id: int, cancel: threading.Event) -> None:
        grouped: dict[str, list[ModAsset]] = {}
        for asset in assets:
            key = f"workshop:{asset.workshop_id}" if asset.workshop_id else f"local:{asset.id}"
            grouped.setdefault(key, []).append(asset)
        groups = list(grouped.values())
        fetched_counts = [0] * len(groups)
        fractions = [0.0] * len(groups)
        result_by_index = {}
        mod_futures = {}

        def progress():
            return round(100 * sum(len(group) * fractions[index] for index, group in enumerate(groups)) / len(assets), 1)

        def report_fetched(index, count):
            with self._lock:
                fetched_counts[index] = count
                self._update(fetched_comments=sum(fetched_counts))

        def report_analyzed(index, count, fraction):
            with self._lock:
                fractions[index] = fraction * 0.9
                self._update(analyzed_comments=self._state["analyzed_comments"] + count, progress=progress())

        def publish(index, result):
            with self._lock:
                result_by_index[index] = result
                if result["status"] != "cancelled":
                    fractions[index] = 1.0
                ordered = [result_by_index[key] for key in sorted(result_by_index)]
                completed = sum(len(item["mod_ids"]) for item in ordered if item["status"] != "cancelled")
                self._update(results=copy.deepcopy(ordered), completed_mods=completed, progress=progress())

        try:
            self._update(cache_bytes=self.cache.size_bytes)
            # MOD orchestration does not occupy AI worker slots; one large MOD can also use all five.
            with ThreadPoolExecutor(max_workers=MAX_AI_WORKERS, thread_name_prefix="mod-ai-agent") as ai_pool, \
                    ThreadPoolExecutor(max_workers=MAX_AI_WORKERS, thread_name_prefix="workshop-mod") as mod_pool:
                for index, group in enumerate(groups):
                    check_cancelled(cancel)
                    future = mod_pool.submit(
                        self._analyze_mod, group, settings, app_id, cancel, ai_pool,
                        lambda count, index=index: report_fetched(index, count),
                        lambda count, fraction, index=index: report_analyzed(index, count, fraction),
                    )
                    mod_futures[future] = index
                for future in as_completed(mod_futures):
                    publish(mod_futures[future], future.result())
                    check_cancelled(cancel)
            results = [result_by_index[index] for index in sorted(result_by_index)]
            check_cancelled(cancel)
            incomplete = any(result["status"] == "error" for result in results)
            self._update(
                status="partial" if incomplete else "completed", running=False, phase="done", progress=100,
                current_mod="", cache_bytes=self.cache.size_bytes,
            )
        except AnalysisCancelled:
            # Pools have joined here. Preserve every MOD that completed while cancellation was pending.
            for future, index in mod_futures.items():
                if index not in result_by_index and future.done() and not future.cancelled():
                    try:
                        publish(index, future.result())
                    except Exception:
                        pass
            self._update(status="cancelled", running=False, phase="done", cache_bytes=self.cache.size_bytes)
        except Exception as exc:
            self._update(status="error", running=False, phase="done", error=self._error_code(exc))
