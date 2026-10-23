"""Check documentation paths and links.

Checks:
  - relative Markdown links resolve to existing files
  - root README/AGENTS no longer contain the stale G:\\git development path
  - docs/superpowers is gone (historical plans live under docs/archive)
  - docs/README.md exists

External links are not fetched. Exit code: 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCE_RE = re.compile(r"```.*?```", re.S)
LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")
STALE_PATH = "G:" + "\\git"
HISTORICAL_PARTS = {"archive", "research", "validation", "audits"}


def _markdown_files() -> list[Path]:
    try:
        output = subprocess.run(
            ["git", "ls-files", "*.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        paths = [ROOT / line.strip() for line in output.splitlines() if line.strip()]
        return sorted(path for path in paths if path.is_file())
    except (OSError, subprocess.SubprocessError):
        return sorted(ROOT.rglob("*.md"))


def _link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    errors: list[str] = []
    warnings: list[str] = []
    checked_links = 0

    for path in _markdown_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(ROOT)
        historical = bool(HISTORICAL_PARTS.intersection(path.parts))

        if path.name in {"README.md", "README.en.md", "AGENTS.md"} and STALE_PATH in text:
            errors.append(f"{relative}: contains stale absolute path {STALE_PATH}")

        body = FENCE_RE.sub("", text)
        for match in LINK_RE.finditer(body):
            raw = match.group(1).strip()
            if not raw or raw.startswith(("http://", "https://", "mailto:", "#", "data:")):
                continue
            target = _link_target(raw)
            target = LINE_SUFFIX_RE.sub("", target)
            if not target:
                continue
            checked_links += 1
            if re.match(r"^[A-Za-z]:[\\/]", target) or Path(target).is_absolute():
                message = f"{relative}: absolute path link {raw!r}"
                (warnings if historical else errors).append(message)
                continue
            resolved = (path.parent / target).resolve(strict=False)
            try:
                display = resolved.relative_to(ROOT).as_posix()
            except ValueError:
                display = str(resolved)
            if not resolved.exists():
                errors.append(f"{relative}: broken link {raw!r} -> {display}")
            elif args.verbose:
                print(f"ok: {relative} -> {raw}")

    if (ROOT / "docs" / "superpowers").exists():
        errors.append("docs/superpowers still exists; historical plans belong under docs/archive")
    if not (ROOT / "docs" / "README.md").is_file():
        errors.append("docs/README.md is missing")

    print(
        f"markdown files: {len(_markdown_files())}, relative links checked: {checked_links}, "
        f"historical absolute-path warnings: {len(warnings)}"
    )
    if args.verbose:
        for warning in warnings:
            print(f"warning: {warning}")
    if errors:
        print("\ndocs check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("docs check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
