"""Run the repository quality checks in one place.

Default checks:
  - version sources
  - i18n catalog structure
  - documentation links
  - frontend/backend RPC contract
  - ruff (when installed)
  - pytest (when backend dependencies are available)
  - frontend vitest (when node_modules is present)

--full also runs the frontend production build.
--ci treats skipped checks as failures.

This script never installs dependencies and never starts a release build.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


@dataclass
class Result:
    label: str
    status: str
    detail: str = ""


def _run(label: str, command: list[str], cwd: Path = ROOT) -> Result:
    print(f"\n== {label} ==")
    print("> " + " ".join(command))
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode == 0:
        return Result(label, "pass")
    return Result(label, "fail", f"exit code {completed.returncode}")


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _backend_deps_ready() -> tuple[bool, str]:
    required = ["lz4", "zstandard", "watchdog", "PIL"]
    if os.name == "nt":
        required.append("webview")
    missing = [name for name in required if not _has_module(name)]
    if missing:
        return False, "missing " + ", ".join(missing)
    return True, ""


def _pnpm() -> str:
    return shutil.which("pnpm.cmd") or shutil.which("pnpm") or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Also run the frontend production build")
    parser.add_argument("--ci", action="store_true", help="Fail when a check is skipped")
    args = parser.parse_args(argv)

    results: list[Result] = []

    static_checks = [
        ("versions", [PYTHON, "scripts/check_versions.py"]),
        ("i18n", [PYTHON, "scripts/check_i18n.py"]),
        ("docs", [PYTHON, "scripts/check_docs.py"]),
        ("rpc contract", [PYTHON, "scripts/check_rpc_contract.py"]),
    ]
    for label, command in static_checks:
        results.append(_run(label, command))

    if _has_module("ruff"):
        results.append(_run("ruff", [PYTHON, "-m", "ruff", "check", "backend", "tests", "scripts"]))
    else:
        results.append(Result("ruff", "skip", "ruff is not installed"))

    backend_ready, backend_detail = _backend_deps_ready()
    if backend_ready:
        results.append(_run("pytest", [PYTHON, "-m", "pytest", "-q"]))
    else:
        results.append(Result("pytest", "skip", backend_detail))

    frontend = ROOT / "frontend"
    pnpm = _pnpm()
    if not (frontend / "node_modules").is_dir():
        results.append(Result("vitest", "skip", "frontend/node_modules is missing"))
    elif not pnpm:
        results.append(Result("vitest", "skip", "pnpm is not installed"))
    else:
        results.append(_run("vitest", [pnpm, "test"], cwd=frontend))
        if args.full:
            results.append(_run("frontend build", [pnpm, "build"], cwd=frontend))

    print("\n== summary ==")
    for result in results:
        detail = f" ({result.detail})" if result.detail else ""
        print(f"  {result.status.upper():4} {result.label}{detail}")

    failures = [result for result in results if result.status == "fail"]
    skipped = [result for result in results if result.status == "skip"]
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    if args.ci and skipped:
        print(f"\n{len(skipped)} check(s) skipped in CI mode")
        return 1
    print("\nall executed checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
