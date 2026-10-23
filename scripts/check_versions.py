"""Verify version sources stay consistent.

Source versions must match:
  - pyproject.toml
  - frontend/package.json
  - backend/constants.py

packaging/update-manifest.json is the installed-client notification switch. Per
the project release rules it may point at the previous downloadable version, so
by default it only has to be a valid version no newer than the source version.
Use --strict-manifest to require equality (release-time check).

Exit code: 0 when consistent, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def _read_pyproject() -> str:
    path = ROOT / "pyproject.toml"
    match = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else ""


def _read_package_json() -> str:
    path = ROOT / "frontend" / "package.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("version", ""))


def _read_constants() -> str:
    path = ROOT / "backend" / "constants.py"
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else ""


def _read_manifest() -> str:
    path = ROOT / "packaging" / "update-manifest.json"
    if not path.is_file():
        return ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("version", ""))


def _semver_tuple(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_RE.match(value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-manifest",
        action="store_true",
        help="Require update-manifest.json to equal the source version",
    )
    args = parser.parse_args(argv)

    sources = {
        "pyproject.toml": _read_pyproject(),
        "frontend/package.json": _read_package_json(),
        "backend/constants.py": _read_constants(),
    }
    errors: list[str] = []

    for name, value in sources.items():
        if not value:
            errors.append(f"{name}: version not found")
        elif _semver_tuple(value) is None:
            errors.append(f"{name}: invalid semver {value!r}")

    source_versions = set(sources.values())
    if len(source_versions) != 1:
        errors.append(f"source versions differ: {sources}")

    manifest = _read_manifest()
    if not manifest:
        errors.append("packaging/update-manifest.json: version not found")
    elif _semver_tuple(manifest) is None:
        errors.append(f"packaging/update-manifest.json: invalid semver {manifest!r}")
    else:
        source = _semver_tuple(next(iter(source_versions))) if len(source_versions) == 1 else None
        manifest_tuple = _semver_tuple(manifest)
        if source and manifest_tuple and manifest_tuple > source:
            errors.append(
                f"update manifest {manifest} is newer than source {next(iter(source_versions))}"
            )
        if args.strict_manifest and manifest != next(iter(source_versions), ""):
            errors.append(
                f"update manifest {manifest} must equal source {next(iter(source_versions), '')}"
            )

    print("version sources:")
    for name, value in sources.items():
        print(f"  {name}: {value or '<missing>'}")
    print(f"  packaging/update-manifest.json: {manifest or '<missing>'}")

    if errors:
        print("\nversion check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("\nversion check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
