"""Test-suite defaults.

Windows can briefly keep SQLite WAL files or scanner handles open while a test
temporary directory is removed. That makes ``TemporaryDirectory.cleanup``
flaky without indicating a product failure. Default to tolerant cleanup for
tests; individual tests can still pass an explicit value.
"""

from __future__ import annotations

import tempfile

import pytest

_ORIGINAL_TEMPORARY_DIRECTORY = tempfile.TemporaryDirectory


class _TolerantTemporaryDirectory(_ORIGINAL_TEMPORARY_DIRECTORY):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("ignore_cleanup_errors", True)
        super().__init__(*args, **kwargs)


@pytest.fixture(autouse=True)
def _tolerant_temporary_directories(monkeypatch):
    monkeypatch.setattr(tempfile, "TemporaryDirectory", _TolerantTemporaryDirectory)
