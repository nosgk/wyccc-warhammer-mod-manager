"""Repository metrics for the harness engineering work.

Standard library only. Produces a Markdown snapshot of code size, largest
modules/functions, backend/frontend test coverage by filename, and version
source consistency. It does not import project modules, so it runs before the
development environment is installed.

Usage:
    python scripts/harness_metrics.py
    python scripts/harness_metrics.py --output docs/harness/metrics.md
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND_SRC = ROOT / "frontend" / "src"
TESTS = ROOT / "tests"
SCRIPTS = ROOT / "scripts"


@dataclass(frozen=True)
class FileMetric:
    path: Path
    lines: int


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _frontend_files() -> list[Path]:
    if not FRONTEND_SRC.is_dir():
        return []
    return sorted(
        path
        for path in FRONTEND_SRC.rglob("*")
        if path.suffix in {".js", ".vue"} and "node_modules" not in path.parts
    )


def _file_metrics(paths: list[Path]) -> list[FileMetric]:
    return sorted(
        (FileMetric(path=path, lines=_line_count(path)) for path in paths),
        key=lambda item: item.lines,
        reverse=True,
    )


def _total_lines(paths: list[Path]) -> int:
    return sum(_line_count(path) for path in paths)


def _largest_functions(root: Path, limit: int = 15) -> list[tuple[str, str, int, int]]:
    rows: list[tuple[str, str, int, int]] = []
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end = getattr(node, "end_lineno", node.lineno)
            rows.append((_relative(path), node.name, node.lineno, end - node.lineno + 1))
    rows.sort(key=lambda item: item[3], reverse=True)
    return rows[:limit]


def _backend_test_map() -> list[tuple[str, bool, str]]:
    modules = sorted(path.stem for path in BACKEND.glob("*.py") if path.name != "__init__.py")
    test_stems = sorted(path.stem[5:] for path in TESTS.glob("test_*.py"))
    result: list[tuple[str, bool, str]] = []
    for module in modules:
        hits = sorted(
            stem for stem in test_stems if module in stem or stem in module
        )
        result.append((module, bool(hits), ", ".join(hits[:3])))
    return result


def _frontend_test_map() -> tuple[list[tuple[str, bool]], list[tuple[str, bool]]]:
    components = sorted(
        path.stem for path in (FRONTEND_SRC / "components").glob("*.vue")
    )
    component_tests = {
        path.name.split(".test.")[0]
        for path in (FRONTEND_SRC / "components" / "__tests__").glob("*.test.js")
    }
    modules = sorted(
        path.stem
        for path in FRONTEND_SRC.glob("*.js")
        if not path.stem.endswith(".test")
    )
    module_tests = {
        path.name.split(".test.")[0]
        for path in (FRONTEND_SRC / "__tests__").glob("*.test.js")
    }
    return (
        [(name, name in component_tests) for name in components],
        [(name, name in module_tests) for name in modules],
    )


def _read_version_sources() -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []

    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(
            r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M
        )
        if match:
            sources.append(("pyproject.toml", match.group(1)))

    package_json = ROOT / "frontend" / "package.json"
    if package_json.is_file():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
            sources.append(("frontend/package.json", str(payload.get("version", ""))))
        except json.JSONDecodeError:
            sources.append(("frontend/package.json", "<invalid json>"))

    constants = BACKEND / "constants.py"
    if constants.is_file():
        match = re.search(
            r'^APP_VERSION\s*=\s*"([^"]+)"',
            constants.read_text(encoding="utf-8"),
            re.M,
        )
        if match:
            sources.append(("backend/constants.py", match.group(1)))

    manifest = ROOT / "packaging" / "update-manifest.json"
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            sources.append(("packaging/update-manifest.json", str(payload.get("version", ""))))
        except json.JSONDecodeError:
            sources.append(("packaging/update-manifest.json", "<invalid json>"))

    return sources


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def render() -> str:
    backend_files = _python_files(BACKEND)
    test_files = _python_files(TESTS)
    script_files = _python_files(SCRIPTS)
    frontend_files = _frontend_files()

    backend_metrics = _file_metrics(backend_files)
    frontend_metrics = _file_metrics(frontend_files)
    largest_functions = _largest_functions(BACKEND)
    backend_map = _backend_test_map()
    component_map, module_map = _frontend_test_map()
    versions = _read_version_sources()

    lines: list[str] = [
        "# Harness 基线度量",
        "",
        "> 由 `python scripts/harness_metrics.py` 生成。仅统计源码与测试，不含 `node_modules`、构建产物与本地数据。",
        "",
        "## 总量",
        "",
    ]
    lines += _table(
        ["范围", "文件数", "行数"],
        [
            ["backend/", str(len(backend_files)), str(_total_lines(backend_files))],
            ["tests/", str(len(test_files)), str(_total_lines(test_files))],
            ["scripts/", str(len(script_files)), str(_total_lines(script_files))],
            ["frontend/src/", str(len(frontend_files)), str(_total_lines(frontend_files))],
        ],
    )
    lines += ["", "## 最大后端文件", ""]
    lines += _table(
        ["文件", "行数"],
        [[_relative(item.path), str(item.lines)] for item in backend_metrics[:15]],
    )
    lines += ["", "## 最大前端文件", ""]
    lines += _table(
        ["文件", "行数"],
        [[_relative(item.path), str(item.lines)] for item in frontend_metrics[:15]],
    )
    lines += ["", "## 最大后端函数", ""]
    lines += _table(
        ["文件", "函数", "起始行", "行数"],
        [
            [path, name, str(line), str(size)]
            for path, name, line, size in largest_functions
        ],
    )

    lines += ["", "## 后端模块测试映射", ""]
    lines += _table(
        ["模块", "有直接测试", "匹配到的测试"],
        [[name, "是" if ok else "否", hits or "-"] for name, ok, hits in backend_map],
    )

    missing_backend = [name for name, ok, _ in backend_map if not ok]
    missing_components = [name for name, ok in component_map if not ok]
    missing_modules = [name for name, ok in module_map if not ok]

    lines += ["", "## 前端测试映射", ""]
    lines += _table(
        ["组件", "有直接测试"],
        [[name, "是" if ok else "否"] for name, ok in component_map],
    )
    lines += [""]
    lines += _table(
        ["模块", "有直接测试"],
        [[name, "是" if ok else "否"] for name, ok in module_map],
    )

    lines += ["", "## 版本号来源", ""]
    lines += _table(["来源", "版本"], [[path, version] for path, version in versions])
    unique_source_versions = {version for path, version in versions if "update-manifest" not in path}
    lines += [
        "",
        f"- 源码版本是否一致：{'是' if len(unique_source_versions) == 1 else '否'}",
        f"- 源码版本集合：{', '.join(sorted(unique_source_versions)) or '-'}",
    ]

    lines += ["", "## 缺口汇总", ""]
    lines.append(f"- 后端无直接测试模块：{len(missing_backend)} 个")
    lines.append(f"- 前端无直接测试组件：{len(missing_components)} 个")
    lines.append(f"- 前端无直接测试模块：{len(missing_modules)} 个")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="", help="Write Markdown to this path")
    args = parser.parse_args(argv)

    report = render()
    if args.output:
        target = Path(args.output)
        if not target.is_absolute():
            target = ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
        print(f"wrote {target}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
