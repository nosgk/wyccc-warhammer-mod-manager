from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import API
from backend.models import GamePaths
from backend.steamworks_bridge import _run_bridge_request, find_node_executable, publish_workshop_item
from tests.helpers import make_asset, write_pack


class WorkshopPublishTagTests(unittest.TestCase):
    """Exercise API -> Python bridge -> Node bridge with an isolated Steam client."""

    def setUp(self) -> None:
        node = find_node_executable()
        if node is None:
            self.skipTest("Node.js runtime is unavailable")
        temporary = tempfile.TemporaryDirectory(prefix="wmm-publish-tags-", ignore_cleanup_errors=True)
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
    supportsKeyValueTags: () => !JSON.parse(fs.readFileSync(trace, 'utf8')).unsupportedKeyValues,
    getSubscribedItems: () => [123n, 456n],
    getItems: async ids => ({ items: [{
      publishedFileId: ids[0],
      creatorAppId: JSON.parse(fs.readFileSync(trace, 'utf8')).creatorAppId || 1142710,
      consumerAppId: 1142710,
      owner: { steamId64: BigInt(JSON.parse(fs.readFileSync(trace, 'utf8')).ownerId || '76561198000000000') },
      tags: JSON.parse(fs.readFileSync(trace, 'utf8')).tags,
      keyValueTags: JSON.parse(fs.readFileSync(trace, 'utf8')).keyValueTags || [],
    }] }),
    createItem: async () => ({ itemId: 456n }),
    updateItem: async (id, details, appId) => {
      const state = JSON.parse(fs.readFileSync(trace, 'utf8'));
      state.last_update = { id: id.toString(), ...details, appId };
      if (Object.prototype.hasOwnProperty.call(details, 'tags')) state.tags = details.tags;
      if (!state.dropKeyValues) state.keyValueTags = (state.keyValueTags || []).filter(
        tag => !(details.removeKeyValueTags || []).includes(tag.key),
      ).concat(details.keyValueTags || []);
      state.update_count = (state.update_count || 0) + 1;
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
        self.addCleanup(shutil.rmtree, self.root / "state", True)
        self.addCleanup(self.api.close)
        self.api._assets = {self.asset.id: self.asset}
        self.payload = {"mode": "update", "title": "Fixture MOD", "language": "en-US"}

        def publish(**kwargs):
            return publish_workshop_item(**kwargs, root=self.root)

        for context in (
            patch.dict(os.environ, {"WMM_PUBLISH_TAG_TEST_STATE": str(self.trace)}),
            patch("backend.steamworks_bridge.find_node_executable", return_value=node),
            patch("backend.api.publish_workshop_item", side_effect=publish),
            patch("backend.steamworks_bridge.read_game_file_version", return_value=589824),
            patch.object(self.api.settings_service, "resolve_game_paths", return_value=GamePaths(game_path=str(self.root))),
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

    def _publish_bridge(self, **overrides):
        content = self.root / "content"
        content.mkdir(exist_ok=True)
        (content / "example.pack").write_bytes(Path(self.asset.path).read_bytes())
        return _run_bridge_request({
            "operation": "publish_item", "appId": 1142710, "id": "123",
            "contentPath": str(content), "previewPath": str(Path(self.asset.path).with_suffix(".png")),
            "title": "Fixture MOD", "caPackName": "example.pack", "caGameVersion": 589824,
            **overrides,
        }, root=self.root)

    def test_upload_includes_ca_identification_in_the_content_update(self) -> None:
        self._publish_bridge(id="")
        state = json.loads(self.trace.read_text(encoding="utf-8"))
        self.assertEqual(state.get("keyValueTags"), [
            {"key": "pack_name", "value": "example.pack"},
            {"key": "game_version", "value": "589824"},
        ])

    def test_update_of_old_item_repairs_missing_ca_fields(self) -> None:
        self._publish_bridge()
        state = json.loads(self.trace.read_text(encoding="utf-8"))
        self.assertEqual(state.get("keyValueTags"), [
            {"key": "pack_name", "value": "example.pack"},
            {"key": "game_version", "value": "589824"},
        ])
        self.assertEqual(state["tags"], self.original_tags)

    def test_update_replaces_case_variants_and_preserves_other_key_values(self) -> None:
        self.trace.write_text(json.dumps({"tags": self.original_tags, "keyValueTags": [
            {"key": "PACK_NAME", "value": "old.pack"},
            {"key": "Game_Version", "value": "524289"},
            {"key": "custom_field", "value": "keep"},
        ]}), encoding="utf-8")
        self._publish_bridge()
        state = json.loads(self.trace.read_text(encoding="utf-8"))
        self.assertEqual(state["keyValueTags"], [
            {"key": "custom_field", "value": "keep"},
            {"key": "pack_name", "value": "example.pack"},
            {"key": "game_version", "value": "589824"},
        ])

    def test_identification_repair_changes_only_key_values_and_is_idempotent(self) -> None:
        self._publish_bridge(operation="repair_ca_metadata")
        state = json.loads(self.trace.read_text(encoding="utf-8"))
        self.assertEqual(set(state["last_update"]), {"id", "appId", "keyValueTags", "removeKeyValueTags"})
        self._publish_bridge(operation="repair_ca_metadata")
        self.assertEqual(json.loads(self.trace.read_text(encoding="utf-8"))["update_count"], 1)

    def test_failed_ca_readback_does_not_report_success(self) -> None:
        self.trace.write_text(json.dumps({"tags": self.original_tags, "dropKeyValues": True}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "readback failed"):
            self._publish_bridge()

    def test_missing_native_support_fails_before_upload(self) -> None:
        self.trace.write_text(json.dumps({"tags": self.original_tags, "unsupportedKeyValues": True}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "does not support CA"):
            self._publish_bridge(id="")
        self.assertNotIn("last_update", json.loads(self.trace.read_text(encoding="utf-8")))

    def test_repair_rejects_other_owners_and_other_games(self) -> None:
        for extra in ({"ownerId": "76561198000000001"}, {"creatorAppId": 779340}):
            with self.subTest(extra=extra):
                self.trace.write_text(json.dumps({"tags": self.original_tags, **extra}), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    self._publish_bridge(operation="repair_ca_metadata")
                self.assertNotIn("last_update", json.loads(self.trace.read_text(encoding="utf-8")))

    def test_other_game_publication_preserves_its_key_values(self) -> None:
        original = [{"key": "game_version", "value": "foreign game convention"}]
        self.trace.write_text(json.dumps({"tags": self.original_tags, "keyValueTags": original}), encoding="utf-8")
        self._publish_bridge(appId=779340)
        state = json.loads(self.trace.read_text(encoding="utf-8"))
        self.assertEqual(state["keyValueTags"], original)
        self.assertNotIn("keyValueTags", state["last_update"])

    def test_api_uses_the_selected_game_executable_for_update_identification(self) -> None:
        with patch("backend.steamworks_bridge.read_game_file_version", return_value=655362) as read_version:
            result = self.api.call("publish_workshop_item", [self.asset.id, self.payload])
        self.assertTrue(result["ok"], result)
        read_version.assert_called_once_with(str(self.root / "Warhammer3.exe"))
        self.assertIn(
            {"key": "game_version", "value": "655362"},
            json.loads(self.trace.read_text(encoding="utf-8"))["keyValueTags"],
        )


if __name__ == "__main__":
    unittest.main()
