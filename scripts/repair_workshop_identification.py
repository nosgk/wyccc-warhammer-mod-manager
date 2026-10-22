"""Repair the exact owned Workshop items in a reviewed manifest; dry-run by default."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.steamworks_bridge import repair_workshop_identification  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--game-executable", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))["items"]
    snapshot = {item["workshop_id"]: item for item in json.loads(
        args.snapshot.read_text(encoding="utf-8"),
    )["items"]}
    ids = [str(item["workshop_id"]) for item in manifest]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate Workshop IDs in repair manifest")
    # Validate the complete scope before making any changes.
    for item in manifest:
        row = snapshot[str(item["workshop_id"])]
        pack = str(item["suggested_pack_name"])
        packs = [name for name in row["install_files"] if name.lower().endswith(".pack")]
        if packs != [pack] or not (Path(row["install_path"]) / pack).is_file():
            raise ValueError(f"Installed Pack differs from reviewed manifest: {item['workshop_id']}")
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "applied": args.apply, "items": []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for item in manifest:
        item_id = str(item["workshop_id"])
        try:
            result = repair_workshop_identification(
                item_id, item["suggested_pack_name"], args.game_executable, dry_run=not args.apply,
            )
            report["items"].append({"title": item["title"], **result})
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            report["items"].append({"workshop_id": item_id, "error": str(exc)})
            print(f"FAILED {item_id}: {exc}", flush=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 1
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
