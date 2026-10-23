from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .constants import INTERNAL_FEATURE_PACK_NAMES
from .models import ModAsset
from .start_options import UNIT_DATA_PATCH_NAME


DISABLED_PACK_SUFFIX = ".wyccc-multiplayer-disabled"


def restore_launcher_packs(data_path: str) -> set[str]:
    """Restore Packs suspended for a previous multiplayer launch."""
    restored_names: set[str] = set()
    for backup in Path(data_path).glob(f"*.pack{DISABLED_PACK_SUFFIX}"):
        target = backup.with_name(backup.name.removesuffix(DISABLED_PACK_SUFFIX))
        restored_names.add(target.name.casefold())
        if target.exists():
            # A settings change may already have rebuilt the active Pack.
            backup.unlink()
        else:
            backup.rename(target)
    return restored_names


def suspend_launcher_packs(
    data_path: str, game_id: str, assets: Iterable[ModAsset], restored_names: Iterable[str] = (),
) -> set[str]:
    """Keep managed Packs out of Data, including movie Packs that auto-load."""
    data_root = Path(data_path).resolve(strict=False)
    names = set(INTERNAL_FEATURE_PACK_NAMES) if game_id == "warhammer3" else set()
    names.update(restored_names)
    for asset in assets:
        if asset.is_launcher_runtime_pack and (
            game_id == "warhammer3" or asset.pack_name.casefold() == UNIT_DATA_PATCH_NAME.casefold()
        ) and Path(asset.path).parent.resolve(strict=False) == data_root:
            names.add(Path(asset.path).name.casefold())
    for path in data_root.glob("*.pack"):
        if path.name.casefold() in names:
            path.replace(path.with_name(path.name + DISABLED_PACK_SUFFIX))
    return names
