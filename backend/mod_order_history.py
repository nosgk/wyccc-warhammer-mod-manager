"""Persistent history for completed MOD order analyses.

Each record keeps the evidence-derived result and the recommended order so the
user can review an earlier analysis or apply it later. Records are small enough
for an atomic JSON file; no game or Workshop data is re-fetched.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .json_store import AtomicJsonStore

MAX_RECORDS = 50
SUMMARY_FIELDS = (
    "id",
    "created_at",
    "completed_at",
    "game_id",
    "total_mods",
    "ai_enabled",
    "degraded",
    "applied_at",
    "model",
    "baseline",
)


class ModOrderHistoryStore:
    def __init__(self, path: Path | None = None, *, max_records: int = MAX_RECORDS) -> None:
        self.store = AtomicJsonStore(path, lambda: {"records": []}) if path is not None else None
        self.max_records = max(1, int(max_records))
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []

    def _payload(self) -> dict[str, Any]:
        if self.store is None:
            return {"records": self._records}
        payload = self.store.load()
        records = payload.get("records")
        if not isinstance(records, list):
            records = []
        return {"records": records}

    def _save(self, payload: dict[str, Any]) -> None:
        if self.store is not None:
            self.store.save(payload)
        else:
            self._records = payload["records"]

    @staticmethod
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        return {field: record.get(field) for field in SUMMARY_FIELDS}

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = self._payload()
            record_id = str(record.get("id") or "")
            records = [
                item for item in payload["records"] if str(item.get("id") or "") != record_id
            ]
            records.insert(0, dict(record))
            payload["records"] = records[: self.max_records]
            self._save(payload)
            return dict(record)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._summary(record) for record in self._payload()["records"]]

    def get(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            for record in self._payload()["records"]:
                if str(record.get("id") or "") == str(record_id):
                    return dict(record)
        raise ValueError("aiOrder.historyNotFound")

    def mark_applied(self, record_id: str, ordered_mod_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            payload = self._payload()
            for record in payload["records"]:
                if str(record.get("id") or "") != str(record_id):
                    continue
                record["applied_at"] = int(time.time() * 1000)
                record["applied_order"] = list(ordered_mod_ids)
                self._save(payload)
                return dict(record)
        raise ValueError("aiOrder.historyNotFound")

    def delete(self, record_id: str) -> bool:
        with self._lock:
            payload = self._payload()
            records = [
                item
                for item in payload["records"]
                if str(item.get("id") or "") != str(record_id)
            ]
            if len(records) == len(payload["records"]):
                return False
            payload["records"] = records
            self._save(payload)
            return True
