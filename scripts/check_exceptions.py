"""Keep broad exception handlers from growing.

The backend still contains legacy ``except Exception`` handlers that need
narrowing. This check freezes the current count as a budget: new broad
handlers fail, while existing ones can be removed incrementally.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BUDGET = 51
BROAD_RE = re.compile(r"^\s*except\s+(?:Exception|BaseException)\s*(?:as\s+\w+)?\s*:")
BARE_RE = re.compile(r"^\s*except\s*:")


def _scan() -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BROAD_RE.match(line) or BARE_RE.match(line):
                findings.append((path, number, line.strip()))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=int, default=BUDGET)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    findings = _scan()
    by_file: dict[str, int] = {}
    for path, _number, _line in findings:
        by_file[path.relative_to(ROOT).as_posix()] = by_file.get(
            path.relative_to(ROOT).as_posix(), 0
        ) + 1

    print(f"broad exception handlers: {len(findings)} (budget {args.budget})")
    if args.verbose:
        for path, number, line in findings:
            print(f"  {path.relative_to(ROOT)}:{number}: {line}")
    else:
        for name, count in sorted(by_file.items(), key=lambda item: (-item[1], item[0]))[:10]:
            print(f"  {name}: {count}")

    if len(findings) > args.budget:
        print("\nbroad exception budget exceeded; narrow the new handlers")
        return 1
    print("broad exception check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
