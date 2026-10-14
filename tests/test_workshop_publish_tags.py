from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import API
from backend.steamworks_bridge import find_node_executable, publish_workshop_item
from tests.helpers import make_asset, write_pack


class WorkshopPublishTagTests(unittest.TestCase):
    """Exercise API -> Python bridge -> Node bridge with an isolated Steam client."""

    def setUp(self) -> None:
        node = find_node_executable()
        if node is None:
            self.skipTest("Node.js runtime is unavailable")
        temporary = tempfile.TemporaryDirectory(prefix="wmm-publish-tags-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        runtime = self.root / "steam_runtime"
        (runtime / "steamworks").mkdir(parents=True)
        (runtime / "workshop_bridge.js").write_bytes(
            (Path(__file__).parents[1] / "steam_runtime" / "workshop_bridge.js").read_bytes()
        )
        (runtime / "steamworks" / "index.js").write_text(
            """
const fs = require('fs');
const trace = process.env.WMM_PUBLISH_TAG_TEST_STATE;
module.exports.init = () => ({
  localplayer: {
    getSteamId: () => ({ steamId64: 76561198000000000n }),
    getName: () => 'Fixture owner',
  },
  workshop: {
    supportsUpdateLanguage: () => true,
    getSubscribedItems: () => [123n, 456n],
    getItems: async () => ({ items: [{
      owner: { steamId64: 76561198000000000n },
      tags: JSON.parse(fs.readFileSync(trace, 'utf8')).tags,
    }] }),
    createItem: async () => ({ itemId: 456n }),
    updateItem: async (id, details, appId) => {
      const state = JSON.parse(fs.readFileSync(trace, 'utf8'));
      state.last_update = { id: id.toString(), ...details, appId };
      if (Object.prototype.hasOwnProperty.call(details, 'tags')) state.tags = details.tags;
      fs.writeFileSync(trace, JSON.stringify(state));
      return {};
    },
  },
});
""",
            encoding="utf-8",
        )
        self.trace = self.root / "tags.json"
        self.original_tags = ["mod", "Campaign", "Scripting"]
        self.trace.write_text(json.dumps({"tags": self.original_tags}), encoding="utf-8")
        pack = write_pack(self.root / "example.pack")
        pack.with_suffix(".png").write_bytes(b"cover fixture")
        self.asset = make_asset(pack, "local", "data", workshop_id="123")
        self.api = API(self.root / "state")
        self.api._assets = {self.asset.id: self.asset}
        self.payload = {"mode": "update", "title": "Fixture MOD", "language": "en-US"}

        def publish(**kwargs):
            return publish_workshop_item(**kwargs, root=self.root)

        for context in (
            patch.dict(os.environ, {"WMM_PUBLISH_TAG_TEST_STATE": str(self.trace)}),
            patch("backend.steamworks_bridge.find_node_executable", return_value=node),
            patch("backend.api.publish_workshop_item", side_effect=publish),
            patch.object(self.api, "_open_uri"),
        ):
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)

    def test_update_without_a_category_preserves_every_existing_tag(self) -> None:
        for local_type, category in (("unknown", None), ("visual", "")):
            with self.subTest(local_type=local_type, category=category):
                self.trace.write_text(json.dumps({"tags": self.original_tags}), encoding="utf-8")
                self.asset.mod_types = [local_type]
                payload = dict(self.payload)
                if category is not None:
                    payload["category"] = category
                result = self.api.call("publish_workshop_item", [self.asset.id, payload])
                self.assertTrue(result["ok"], result)
                state = json.loads(self.trace.read_text(encoding="utf-8"))
                self.assertEqual(state["tags"], self.original_tags)
                self.assertNotIn("tags", state["last_update"])

    def test_explicit_category_change_retains_non_category_tags(self) -> None:
        result = self.api.call("publish_workshop_item", [self.asset.id, {**self.payload, "category": "units"}])
        self.assertTrue(result["ok"], result)
        state = json.loads(self.trace.read_text(encoding="utf-8"))
        self.assertEqual(state["tags"], ["mod", "Scripting", "units"])

    def test_upload_without_a_known_category_does_not_publish_as_graphical(self) -> None:
        for local_type in ("unknown", "feature", "language"):
            with self.subTest(local_type=local_type):
                self.asset.workshop_id = ""
                self.trace.write_text(json.dumps({"tags": self.original_tags}), encoding="utf-8")
                self.asset.mod_types = [local_type]
                result = self.api.call("publish_workshop_item", [self.asset.id, {**self.payload, "mode": "upload"}])
                self.assertFalse(result["ok"], result)
                self.assertNotIn("last_update", json.loads(self.trace.read_text(encoding="utf-8")))

    def test_upload_with_a_known_local_type_uses_that_category(self) -> None:
        self.asset.workshop_id = ""
        self.asset.mod_types = ["unit"]
        result = self.api.call("publish_workshop_item", [self.asset.id, {**self.payload, "mode": "upload"}])
        self.assertTrue(result["ok"], result)
        self.assertEqual(json.loads(self.trace.read_text(encoding="utf-8"))["tags"], ["mod", "units"])


if __name__ == "__main__":
    unittest.main()
