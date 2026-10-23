"""Check the frontend/backend RPC contract.

Backend: parse the API._rpc allowlist in backend/api.py.
Frontend: collect literal invoke('method') calls outside tests.
Dynamic calls such as invoke(method, ...) are counted but cannot be checked
statically; they are reported as skipped.

Exit code: 0 when every literal frontend call is registered, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "backend" / "api.py"
FRONTEND = ROOT / "frontend" / "src"

RPC_ENTRY_RE = re.compile(r'^\s*"(?P<name>[a-z][a-z0-9_]*)":\s*self\.', re.M)
INVOKE_LITERAL_RE = re.compile(r"""\binvoke\(\s*(['"])(?P<name>[a-z][a-z0-9_]*)\1""")
INVOKE_DYNAMIC_RE = re.compile(r"""\binvoke\(\s*(?!['"])""")


def _rpc_block(text: str) -> str:
    start = text.index("self._rpc: dict")
    brace_start = text.index("{", start)
    depth = 0
    for index in range(brace_start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start : index + 1]
    raise ValueError("unterminated _rpc block")


def _backend_methods() -> list[str]:
    text = API.read_text(encoding="utf-8")
    return RPC_ENTRY_RE.findall(_rpc_block(text))


def _frontend_calls() -> tuple[set[str], int]:
    literals: set[str] = set()
    dynamic = 0
    for path in sorted(FRONTEND.rglob("*")):
        if path.suffix not in {".js", ".vue"}:
            continue
        if "__tests__" in path.parts or path.name.endswith(".test.js"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        literals.update(match.group("name") for match in INVOKE_LITERAL_RE.finditer(text))
        dynamic += len(INVOKE_DYNAMIC_RE.findall(text))
    return literals, dynamic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    methods = _backend_methods()
    duplicates = sorted({name for name in methods if methods.count(name) > 1})
    registered = set(methods)
    literals, dynamic = _frontend_calls()
    missing = sorted(literals - registered)

    print(f"backend RPC methods: {len(methods)} ({len(registered)} unique)")
    print(f"frontend literal calls: {len(literals)}, dynamic calls: {dynamic}")

    if args.verbose:
        for name in sorted(registered):
            print(f"  rpc: {name}")
        for name in sorted(literals):
            print(f"  call: {name}")

    errors: list[str] = []
    if duplicates:
        errors.append(f"duplicate RPC entries: {', '.join(duplicates)}")
    for name in missing:
        errors.append(f"frontend calls unregistered RPC: {name}")

    if errors:
        print("\nRPC contract check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("RPC contract check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
