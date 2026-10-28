from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from scripts import build


def test_packaged_ssl_can_initialize_after_relocating_the_standard_library(tmp_path):
    files = [
        "frontend/dist/index.html",
        "packaging/wmm.ico",
        "packaging/version_info.txt",
        "backend/wh3_db_schema.json",
        "backend/tower_compatibility_schema.json",
        "backend/schemas/LICENSE.txt",
        "backend/schemas/manifest.json",
        "steam_runtime/workshop_bridge.js",
        "steam_runtime/steamworks/dist/win64/steamworksjs.win32-x64-msvc.node",
        "steam_runtime/steamworks_dependencies/dist/win64/steamworksjs.win32-x64-msvc.node",
        "steam_runtime/steamworks_dependencies/dist/win64/steam_api64.dll",
        "node.exe",
    ]
    files.extend(f"backend/schemas/schema_{game}.ron.gz" for game in build.SCHEMA_NAMES.values())
    for relative in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    commands = []

    def capture_package(command, **_kwargs):
        commands.append(command)
        output = Path(command[command.index("--distpath") + 1])
        name = command[command.index("--name") + 1]
        (output / f"{name}.exe").write_bytes(b"package-fixture")

    with (
        patch.multiple(
            build,
            ROOT=tmp_path,
            FRONTEND=tmp_path / "frontend",
            PACKAGING=tmp_path / "packaging",
            STEAM_RUNTIME=tmp_path / "steam_runtime",
        ),
        patch.object(build, "find_node_for_packaging", return_value=tmp_path / "node.exe"),
        patch.object(build, "run", side_effect=capture_package),
    ):
        build.package_desktop(tmp_path / "release")

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    shutil.copy2(ssl.__file__, bundle / "ssl.py")
    separator = ";" if os.name == "nt" else ":"
    command = commands[-1]
    for index, argument in enumerate(command):
        if argument == "--add-data":
            source, destination = command[index + 1].rsplit(separator, 1)
            if destination == "." and Path(source).is_file():
                shutil.copy2(source, bundle)

    # Execute the real runtime's ssl code at its frozen location. Merely
    # importing ssl succeeds even when a file needed by SSLContext is absent.
    probe = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ssl
from urllib.request import build_opener
assert Path(ssl.__file__).parent == Path(sys.argv[1])
build_opener()
context = ssl.create_default_context()
assert context.verify_mode == ssl.CERT_REQUIRED
assert context.check_hostname
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(bundle)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
