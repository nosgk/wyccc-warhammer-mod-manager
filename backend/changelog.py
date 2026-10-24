from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_CHANGELOG_LANGUAGE = "zh-CN"
SUPPORTED_CHANGELOG_LANGUAGES = frozenset(
    {"zh-CN", "en-US", "ko-KR", "ru-RU", "ja-JP", "es-ES"}
)
_DATA_PATH = Path(__file__).with_name("resources") / "changelog.json"


def _load_data() -> dict[str, Any]:
    payload = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("changelog data must be an object")
    structure = payload.get("structure")
    text = payload.get("text")
    if not isinstance(structure, list) or not isinstance(text, dict):
        raise ValueError("changelog data is incomplete")
    return payload


_DATA = _load_data()
CHANGELOG_STRUCTURE: tuple[dict[str, Any], ...] = tuple(_DATA["structure"])
CHANGELOG_TEXT: dict[str, dict[str, str]] = {
    str(language): {str(key): str(value) for key, value in values.items()}
    for language, values in _DATA["text"].items()
    if isinstance(values, dict)
}


def _build_changelog(language: str) -> list[dict[str, Any]]:
    catalog = CHANGELOG_TEXT[language]
    releases: list[dict[str, Any]] = []
    for release in CHANGELOG_STRUCTURE:
        entries = []
        for title_key, changes_spec in release["entries"]:
            entries.append(
                {
                    "title": catalog[title_key],
                    "changes": [
                        {"type": change_type, "text": catalog[text_key]}
                        for change_type, text_key in changes_spec
                    ],
                }
            )
        releases.append(
            {
                "version": release["version"],
                "date": release["date"],
                "entries": entries,
            }
        )
    return releases


APP_CHANGELOG: list[dict[str, Any]] = _build_changelog(DEFAULT_CHANGELOG_LANGUAGE)


def get_all_changelogs(language: str = DEFAULT_CHANGELOG_LANGUAGE) -> list[dict[str, Any]]:
    """Return an isolated changelog in one of the built-in interface languages."""
    normalized = (
        language if language in SUPPORTED_CHANGELOG_LANGUAGES else DEFAULT_CHANGELOG_LANGUAGE
    )
    return deepcopy(_build_changelog(normalized))
