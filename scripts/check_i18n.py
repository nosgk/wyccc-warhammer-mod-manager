"""Check the six built-in language catalogs for structural drift.

Rules:
  - languages.js base entries are [zh-CN, en-US, ko-KR, ru-RU, ja-JP]
  - auxiliary *Languages.js entries are the same five plus es-ES
  - every base key has a Spanish entry (explicit or from an auxiliary catalog)
  - every variant is non-empty
  - placeholder sets ({name}) match across all languages

This is a structural check, not a translation-quality check.
Exit code: 0 when consistent, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
BASE_FILE = SRC / "languages.js"
AUX_FILES = sorted(
    path
    for path in SRC.glob("*Languages.js")
    if path.name != "languages.js"
)

KEY_RE = re.compile(
    r"""(?P<quote>['"])(?P<key>[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)(?P=quote)\s*:\s*"""
)
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
ARRAY_FILL_RE = re.compile(r"Array\(\s*(\d+)\s*\)\.fill\(\s*(['\"])(.*?)\2\s*\)", re.S)
DERIVED_RE = re.compile(r"""entries\[\s*['"]([^'"]+)['"]\s*\]\s*\[\s*5\s*\]""")


@dataclass
class Entry:
    key: str
    values: list[str]
    line: int
    file: Path


@dataclass
class Catalog:
    arrays: dict[str, Entry] = field(default_factory=dict)
    strings: dict[str, Entry] = field(default_factory=dict)
    derived: dict[str, tuple[str, int]] = field(default_factory=dict)
    duplicates: list[tuple[str, Path, int]] = field(default_factory=list)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _parse_string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    if quote not in {"'", '"'}:
        raise ValueError(f"expected string at offset {start}")
    index = start + 1
    chunks: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text):
                break
            escape = text[index + 1]
            mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"'}
            chunks.append(mapping.get(escape, escape))
            index += 2
            continue
        if char == quote:
            return "".join(chunks), index + 1
        chunks.append(char)
        index += 1
    raise ValueError(f"unterminated string at offset {start}")


def _parse_array(text: str, start: int) -> tuple[list[str], int]:
    if text[start] != "[":
        raise ValueError(f"expected array at offset {start}")
    index = start + 1
    values: list[str] = []
    current: list[str] = []
    depth = 0
    while index < len(text):
        char = text[index]
        if char in {"'", '"'} and depth == 0 and not "".join(current).strip():
            value, index = _parse_string(text, index)
            values.append(value)
            current = []
            continue
        if char == "[":
            depth += 1
            current.append(char)
            index += 1
            continue
        if char == "]":
            if depth == 0:
                if "".join(current).strip():
                    raise ValueError(f"unexpected array content at offset {index}")
                return values, index + 1
            depth -= 1
            current.append(char)
            index += 1
            continue
        if char == "," and depth == 0:
            if "".join(current).strip():
                raise ValueError(f"non-string array element at offset {index}")
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    raise ValueError(f"unterminated array at offset {start}")


def _scan(path: Path) -> Catalog:
    text = path.read_text(encoding="utf-8")
    catalog = Catalog()
    for match in KEY_RE.finditer(text):
        key = match.group("key")
        cursor = match.end()
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(text):
            continue
        char = text[cursor]
        try:
            if char == "[":
                values, _ = _parse_array(text, cursor)
                entry = Entry(key=key, values=values, line=_line_of(text, match.start()), file=path)
                if key in catalog.arrays:
                    catalog.duplicates.append((key, path, entry.line))
                catalog.arrays[key] = entry
            elif char in {"'", '"'}:
                value, _ = _parse_string(text, cursor)
                entry = Entry(key=key, values=[value], line=_line_of(text, match.start()), file=path)
                if key in catalog.strings:
                    catalog.duplicates.append((key, path, entry.line))
                catalog.strings[key] = entry
            elif text.startswith("Array(", cursor):
                fill = ARRAY_FILL_RE.match(text, cursor)
                if fill is None:
                    continue
                values = [fill.group(3)] * int(fill.group(1))
                entry = Entry(key=key, values=values, line=_line_of(text, match.start()), file=path)
                if key in catalog.arrays:
                    catalog.duplicates.append((key, path, entry.line))
                catalog.arrays[key] = entry
            elif text.startswith("entries[", cursor):
                derived = DERIVED_RE.match(text, cursor)
                if derived is not None:
                    catalog.derived[key] = (derived.group(1), _line_of(text, match.start()))
        except ValueError as exc:
            print(f"{path.relative_to(ROOT)}:{_line_of(text, match.start())}: {exc}")
            raise
    return catalog


def _placeholders(values: list[str]) -> set[str]:
    return {name for value in values for name in PLACEHOLDER_RE.findall(value)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    errors: list[str] = []
    warnings: list[str] = []

    base = _scan(BASE_FILE)
    aux_catalogs = [(path, _scan(path)) for path in AUX_FILES]

    aux_arrays: dict[str, Entry] = {}
    for path, catalog in aux_catalogs:
        for key, entry in catalog.arrays.items():
            if key in aux_arrays:
                warnings.append(
                    f"duplicate auxiliary key {key}: {aux_arrays[key].file.name} and {path.name}"
                )
            aux_arrays[key] = entry
        for key, entry in catalog.strings.items():
            warnings.append(
                f"{path.name}:{entry.line}: unexpected string entry for {key} "
                "(auxiliary catalogs should use six-element arrays)"
            )

    for catalog_name, catalog in [("languages.js", base), *[(p.name, c) for p, c in aux_catalogs]]:
        for key, entry in catalog.arrays.items():
            if catalog_name == "languages.js":
                allowed = {5, 6}
                if len(entry.values) not in allowed:
                    errors.append(
                        f"{catalog_name}:{entry.line}: {key} has {len(entry.values)} values, expected 5 or 6"
                    )
            elif len(entry.values) != 6:
                errors.append(
                    f"{catalog_name}:{entry.line}: {key} has {len(entry.values)} values, expected 6"
                )
            if any(not value.strip() for value in entry.values):
                errors.append(f"{catalog_name}:{entry.line}: {key} contains an empty value")
            placeholders = _placeholders(entry.values)
            per_variant = [set(PLACEHOLDER_RE.findall(value)) for value in entry.values]
            if any(item != placeholders for item in per_variant):
                errors.append(
                    f"{catalog_name}:{entry.line}: {key} placeholder mismatch: {per_variant}"
                )

    full_keys = set(base.arrays) | set(aux_arrays)
    spanish_keys = set(base.strings) | set(base.derived)

    for key in sorted(full_keys):
        base_entry = base.arrays.get(key)
        aux_entry = aux_arrays.get(key)
        if base_entry and aux_entry:
            if base_entry.values != aux_entry.values[:5]:
                warnings.append(
                    f"{key}: languages.js and {aux_entry.file.name} disagree on the first five languages"
                )

        spanish_value = ""
        spanish_source = ""
        if aux_entry:
            spanish_value = aux_entry.values[5]
            spanish_source = f"{aux_entry.file.name}:{aux_entry.line}"
        elif base_entry and len(base_entry.values) == 6:
            spanish_value = base_entry.values[5]
            spanish_source = f"languages.js:{base_entry.line}"
        elif key in base.strings:
            spanish_value = base.strings[key].values[0]
            spanish_source = f"languages.js:{base.strings[key].line}"
        elif key in base.derived:
            source_key, line = base.derived[key]
            source_entry = base.arrays.get(source_key) or aux_arrays.get(source_key)
            if source_entry and len(source_entry.values) >= 6:
                spanish_value = source_entry.values[5]
                spanish_source = f"languages.js:{line} (from {source_key})"
            else:
                errors.append(
                    f"languages.js:{line}: {key} derives Spanish from {source_key}, "
                    "but that entry has no Spanish value"
                )
                continue
        else:
            errors.append(f"languages.js: missing Spanish entry for {key}")
            continue

        if not spanish_value.strip():
            errors.append(f"{spanish_source}: {key} has an empty Spanish value")
            continue
        base_values = base_entry.values[:5] if base_entry else (
            aux_entry.values[:5] if aux_entry else [spanish_value]
        )
        if _placeholders([spanish_value]) != _placeholders(base_values):
            errors.append(f"{spanish_source}: {key} Spanish placeholder mismatch")

    for key in sorted(spanish_keys - full_keys):
        warnings.append(f"languages.js: Spanish entry without a base key: {key}")

    for key, path, line in base.duplicates:
        warnings.append(f"{path.name}:{line}: duplicate/override key {key}")

    duplicate_count = len(base.duplicates) + sum(len(c.duplicates) for _, c in aux_catalogs)

    print(
        f"base keys: {len(base.arrays)}, auxiliary keys: {len(aux_arrays)}, "
        f"Spanish strings: {len(base.strings)}, files: {len(AUX_FILES) + 1}"
    )
    if args.verbose:
        for warning in warnings:
            if "duplicate/override key" not in warning:
                print(f"warning: {warning}")
        if duplicate_count:
            print(f"note: {duplicate_count} duplicate/override key(s) from Object.assign blocks")
    if errors:
        print("\ni18n check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"i18n check passed ({len(warnings) - duplicate_count} warning(s), {duplicate_count} override(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
