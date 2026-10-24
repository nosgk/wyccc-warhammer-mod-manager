from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from backend.mod_order_evidence import collect_evidence
from backend.workshop_comments import AnalysisCancelled
from tests.helpers import make_asset, write_pack


class FakeWorkshopService:
    def __init__(self, records):
        self.records = records

    def get_many(self, workshop_ids, language="en-US", *, app_id=1142710):
        return {workshop_id: self.records[workshop_id] for workshop_id in workshop_ids if workshop_id in self.records}


class FakeCommentsClient:
    def __init__(self, comments):
        self.comments = comments

    def recent_comments(self, workshop_id, app_id, cancel, progress):
        return {"comments": self.comments}


class EvidenceCollectionTests(unittest.TestCase):
    def test_collects_pack_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = write_pack(
                root / "mod.pack",
                entries=[
                    ("db/units_tables/data__units", b"db"),
                    ("script/campaign/mod/order.lua", b"script"),
                    ("localisation/zh.tsv", b"loc"),
                    ("ui/units/icons/x.png", b"ui"),
                    ("variantmeshes/variants/x.variantmeshdefinition", b"variant"),
                ],
            )
            asset = make_asset(pack, "mod", "data")
            result = collect_evidence([asset])
            item = result.items[0]
            self.assertEqual(item.db_tables, ("db/units_tables/data__units",))
            self.assertEqual(item.script_paths, ("script/campaign/mod/order.lua",))
            self.assertEqual(item.localization_paths, ("localisation/zh.tsv",))
            self.assertEqual(item.ui_paths, ("ui/units/icons/x.png",))
            self.assertEqual(item.variant_paths, ("variantmeshes/variants/x.variantmeshdefinition",))

    def test_uses_workshop_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = write_pack(Path(temporary) / "mod.pack")
            asset = make_asset(pack, "mod", "workshop", "100")
            service = FakeWorkshopService(
                {
                    "100": {
                        "title": "Workshop title",
                        "description": "Workshop description",
                        "author": "Author",
                        "updated_at": 123,
                        "required_workshop_items": [{"workshop_id": "200"}],
                    }
                }
            )
            result = collect_evidence([asset], workshop_service=service)
            item = result.items[0]
            self.assertEqual(item.title, "Workshop title")
            self.assertEqual(item.description, "Workshop description")
            self.assertEqual(item.author, "Author")
            self.assertEqual(item.required_workshop_items, ("200",))

    def test_filters_order_relevant_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = write_pack(Path(temporary) / "mod.pack")
            asset = make_asset(pack, "mod", "workshop", "100")
            client = FakeCommentsClient(
                [
                    {"text": "This mod crashes unless it loads after the framework"},
                    {"text": "Great mod, thanks!"},
                    {"text": "前置 MOD 需要先加载"},
                ]
            )
            result = collect_evidence([asset], comments_client=client, include_comments=True)
            self.assertEqual(len(result.items[0].comments), 2)
            self.assertEqual(result.comments_fetched, 2)

    def test_missing_pack_is_tolerated(self) -> None:
        asset = make_asset(Path(tempfile.gettempdir()) / "missing.pack", "mod", "data")
        result = collect_evidence([asset])
        self.assertEqual(result.items[0].entry_names, ())
        self.assertEqual(result.warnings, [])

    def test_cancellation_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = write_pack(Path(temporary) / "mod.pack")
            asset = make_asset(pack, "mod", "data")
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(AnalysisCancelled):
                collect_evidence([asset], cancel=cancel)


if __name__ == "__main__":
    unittest.main()
