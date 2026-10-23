"""Preflight the local development environment.

Checks Python, Node, pnpm, backend imports, and frontend dependencies. It does
not install anything. Exit code: 0 when ready, 1 when something is missing
(use --soft to report without failing).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)
MIN_NODE = 22
PYTHON_IMPORTS = [
    ("lz4", "lz4"),
    ("zstandard", "zstandard"),
    ("watchdog", "watchdog"),
    ("PIL", "pillow"),
]
WINDOWS_ONLY_IMPORTS = [("webview", "pywebview")]


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _major_version(text: str) -> int | None:
    digits = ""
    for char in text.lstrip("v"):
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) if digits else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soft", action="store_true", help="Report but do not fail")
    args = parser.parse_args(argv)

    errors: list[str] = []
    notes: list[str] = []

    if sys.version_info < MIN_PYTHON:
        errors.append(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is too old; "
            f"require {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"
        )
    else:
        notes.append(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        errors.append("Node.js not found on PATH; require 22+")
    else:
        version = _run([node, "--version"])
        major = _major_version(version)
        if major is None or major < MIN_NODE:
            errors.append(f"Node.js {version or '<unknown>'} is too old; require {MIN_NODE}+")
        else:
            notes.append(f"Node.js {version}")

    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if not pnpm:
        errors.append("pnpm not found on PATH; require 11+")
    else:
        version = _run([pnpm, "--version"])
        major = _major_version(version)
        if major is None or major < 11:
            errors.append(f"pnpm {version or '<unknown>'} is too old; require 11+")
        else:
            notes.append(f"pnpm {version}")

    imports = list(PYTHON_IMPORTS)
    if os.name == "nt":
        imports.extend(WINDOWS_ONLY_IMPORTS)
    for module, package in imports:
        if importlib.util.find_spec(module) is None:
            errors.append(f"Python package missing: {package} (import {module})")
        else:
            notes.append(f"import {module}: ok")

    node_modules = ROOT / "frontend" / "node_modules"
    if not node_modules.is_dir():
        errors.append("frontend/node_modules is missing; run pnpm install in frontend/")
    else:
        notes.append("frontend/node_modules: ok")

    print("environment:")
    for note in notes:
        print(f"  ok: {note}")
    if errors:
        print("\nmissing:")
        for error in errors:
            print(f"  - {error}")
        print("\nsetup:")
        print("  python -m venv .venv")
        print("  .\\.venv\\Scripts\\Activate.ps1")
        print("  python -m pip install -e \".[dev]\"")
        print("  cd frontend && pnpm install")
        if args.soft:
            print("\nenvironment check reported issues (soft mode)")
            return 0
        return 1
    print("\nenvironment check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
