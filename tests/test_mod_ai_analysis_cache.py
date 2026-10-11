from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.mod_ai_analysis_cache import MAX_CACHE_BYTES, ModAIAnalysisCache, analysis_cache_key


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_disk_cache_survives_restart_and_evicts_least_recently_used_entries(tmp_path):
    cache = ModAIAnalysisCache(tmp_path, max_bytes=700)
    value = "中" * 100
    assert cache.put(key("first"), value)
    assert cache.put(key("second"), value)
    assert cache.get(key("first")) == value
    assert cache.put(key("third"), value)
    assert cache.get(key("second")) is None
    assert cache.get(key("first")) == value
    assert cache.size_bytes == sum(path.stat().st_size for path in tmp_path.glob("*.json")) <= 700
    restored = ModAIAnalysisCache(tmp_path, max_bytes=700)
    assert restored.get(key("first")) == restored.get(key("third")) == value
    assert restored.get(key("second")) is None
    assert not list(tmp_path.glob("*.tmp"))


def test_startup_prunes_oversized_existing_cache_by_persisted_access_time(tmp_path):
    cache = ModAIAnalysisCache(tmp_path)
    for index in range(4):
        entry_key = key(str(index))
        assert cache.put(entry_key, "中" * 100)
        os.utime(tmp_path / f"{entry_key}.json", (1_700_000_000 + index, 1_700_000_000 + index))
    restored = ModAIAnalysisCache(tmp_path, max_bytes=700)
    assert restored.size_bytes <= 700
    assert restored.get(key("0")) is restored.get(key("1")) is None
    assert restored.get(key("2")) == restored.get(key("3")) == "中" * 100


def test_oversized_single_entry_does_not_evict_useful_cached_text(tmp_path):
    cache = ModAIAnalysisCache(tmp_path, max_bytes=100)
    assert cache.put(key("keep"), "cached")
    before = cache.size_bytes
    assert not cache.put(key("too-large"), "中" * 100)
    assert cache.get(key("keep")) == "cached" and cache.size_bytes == before
    assert cache.get(key("too-large")) is None


def test_corrupt_entry_is_removed_and_interrupted_writes_are_cleaned_without_touching_other_files(tmp_path):
    cache = ModAIAnalysisCache(tmp_path)
    assert cache.put(key("broken"), "text")
    (tmp_path / f"{key('broken')}.json").write_text("{", encoding="utf-8")
    assert cache.get(key("broken")) is None
    assert cache.size_bytes == 0
    temporary = tmp_path / f".{key('partial')}.1234.tmp"
    temporary.write_text("partial", encoding="utf-8")
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    assert ModAIAnalysisCache(tmp_path).size_bytes == 0
    assert not temporary.exists() and unrelated.read_text(encoding="utf-8") == "keep"


def test_read_only_cache_falls_back_without_breaking_analysis(tmp_path):
    cache = ModAIAnalysisCache(tmp_path / "cache")
    with patch.object(Path, "mkdir", side_effect=PermissionError):
        assert not cache.put(key("test"), "value")
    assert cache.get(key("test")) is None


@pytest.mark.parametrize("setting, changed", [
    ("ai_model", "other-model"), ("ai_base_url", "https://other.invalid/v1"),
    ("language", "en-US"), ("ai_temperature", 0.8),
])
def test_keys_invalidate_changed_generation_settings_without_using_credentials(setting, changed):
    settings = {"ai_model": "model", "ai_base_url": "https://example.invalid/v1", "language": "zh-CN"}
    original = analysis_cache_key("comment", settings, {"text": "report"})
    assert analysis_cache_key("comment", {**settings, setting: changed}, {"text": "report"}) != original
    assert analysis_cache_key("comment", {**settings, "ai_api_key": "test-only-credential"}, {"text": "report"}) == original
    assert analysis_cache_key("comment", settings, {"text": "edited report"}) != original
    assert analysis_cache_key("summary", settings, {"text": "report"}) != original
    assert ModAIAnalysisCache(None).max_bytes == MAX_CACHE_BYTES == 100 * 1024 * 1024
