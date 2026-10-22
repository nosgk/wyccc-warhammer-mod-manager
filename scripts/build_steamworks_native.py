"""Rebuild the pinned Steamworks bridge with the checked-in local patches."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "steam_runtime/steamworks/patches"


def run(*args: str, cwd: Path = ROOT) -> bytes:
    return subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE).stdout


def main() -> None:
    for kind, repo, commit in (
        ("rs", "Noxime/steamworks-rs", "fbb79635b06b4feea8261e5ca3e8ea3ef42facf9"),
        ("js", "ceifa/steamworks.js", "80c5fd7c4958ba90cf87403c3f967b8a6d748473"),
    ):
        source = ROOT / f".build-tools/steamworks-workshop-{kind}"
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            run("git", "clone", "--no-checkout", f"https://github.com/{repo}", str(source))
            run("git", "checkout", "--detach", commit, cwd=source)
        if run("git", "rev-parse", "HEAD", cwd=source).decode().strip() != commit:
            raise RuntimeError(f"Unexpected source revision: {source}")
        patch = PATCHES / f"steamworks-{kind}.patch"
        # A repeated build accepts only this exact patch, never unrelated edits.
        diff = run("git", "diff", "--no-ext-diff", cwd=source).replace(b"\r\n", b"\n")
        expected = patch.read_bytes().replace(b"\r\n", b"\n")
        if diff and diff != expected:
            raise RuntimeError(f"Unrelated changes in native source: {source}")
        if not diff:
            run("git", "apply", str(patch), cwd=source)
    source = ROOT / ".build-tools/steamworks-workshop-js"
    if (source / "Cargo.lock").read_bytes().replace(b"\r\n", b"\n") != (
        PATCHES / "Cargo.lock"
    ).read_bytes().replace(b"\r\n", b"\n"):
        raise RuntimeError("The native source lockfile differs from the checked-in build lockfile")
    environment = os.environ.copy()
    # Keep the previous GNU build's independence from a separately installed VC runtime.
    environment["RUSTFLAGS"] = "-C target-feature=+crt-static"
    subprocess.run(
        ["cargo", "build", "--release", "--target", "x86_64-pc-windows-msvc", "--locked"],
        cwd=source, env=environment, check=True,
    )
    sdk = ROOT / ".build-tools/steamworks-workshop-rs/steamworks-sys/lib/steam/redistributable_bin/win64"
    output = ROOT / "steam_runtime/steamworks/dist/win64"
    if (sdk / "steam_api64.dll").read_bytes() != (output / "steam_api64.dll").read_bytes():
        raise RuntimeError("The pinned SDK redistributable differs from the bundled Steam DLL")
    target = output / "steamworksjs.win32-x64-msvc.node"
    shutil.copy2(source / "target/x86_64-pc-windows-msvc/release/steamworksjs.dll", target)
    print(f"Native bridge SHA-256: {hashlib.sha256(target.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
