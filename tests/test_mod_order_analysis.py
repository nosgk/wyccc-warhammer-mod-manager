from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.mod_order_analysis import ModOrderAnalysisService
from backend.workshop_comments import AnalysisCancelled
from tests.helpers import make_asset, write_pack


def _wait(service: ModOrderAnalysisService, session_id: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = service.status(session_id)
        if not state["running"]:
            return state
        time.sleep(0.01)
    raise AssertionError("analysis did not finish")


class _NoCommentsClient:
    def recent_comments(self, workshop_id, app_id, cancel, progress):
        return {"comments": []}


class ModOrderAnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"

    def _service(self) -> ModOrderAnalysisService:
        return ModOrderAnalysisService(self.cache, comments_client=_NoCommentsClient())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _assets(self) -> list:
        first = make_asset(write_pack(self.root / "a.pack"), "a", "workshop", "100")
        second = make_asset(write_pack(self.root / "b.pack"), "b", "workshop", "200")
        first = replace(first, required_workshop_items=[{"workshop_id": "200"}])
        return [first, second]

    def _plain_assets(self) -> list:
        return [
            make_asset(write_pack(self.root / "a.pack"), "a", "data"),
            make_asset(write_pack(self.root / "b.pack"), "b", "data"),
        ]

    def test_deterministic_analysis_orders_dependencies(self) -> None:
        service = self._service()
        assets = self._assets()
        initial = service.start(
            assets,
            {"ai_enabled": False, "language": "en-US"},
            game_id="warhammer3",
            current_order=["a", "b"],
        )
        state = _wait(service, initial["id"])
        self.assertEqual(state["status"], "ready")
        self.assertFalse(state["degraded"])
        self.assertEqual(state["result"]["recommended_order"], ["b", "a"])
        self.assertEqual(service.apply(initial["id"]), ["b", "a"])

    def test_ai_relations_are_applied(self) -> None:
        service = self._service()
        assets = self._plain_assets()
        settings = {
            "ai_enabled": True,
            "ai_base_url": "https://example.invalid/v1",
            "ai_model": "example-model",
            "language": "en-US",
        }
        relations = [
            {
                "before": "a",
                "after": "b",
                "relation": "should_before",
                "reason": "framework before content",
                "confidence": 0.9,
            }
        ]
        with patch(
            "backend.mod_order_analysis.analyze_mod_order_relations", return_value=relations
        ):
            initial = service.start(
                assets, settings, game_id="warhammer3", current_order=["b", "a"]
            )
            state = _wait(service, initial["id"])
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["result"]["recommended_order"], ["a", "b"])

    def test_ai_failure_degrades_to_deterministic_result(self) -> None:
        service = self._service()
        assets = self._plain_assets()
        settings = {
            "ai_enabled": True,
            "ai_base_url": "https://example.invalid/v1",
            "ai_model": "example-model",
            "language": "en-US",
        }
        with patch(
            "backend.mod_order_analysis.analyze_mod_order_relations",
            side_effect=ValueError("offline"),
        ):
            initial = service.start(
                assets, settings, game_id="warhammer3", current_order=["a", "b"]
            )
            state = _wait(service, initial["id"])
        self.assertEqual(state["status"], "ready")
        self.assertTrue(state["degraded"])
        self.assertTrue(any("AI ordering unavailable" in warning for warning in state["warnings"]))
        self.assertEqual(state["result"]["recommended_order"], ["a", "b"])

    def test_cancel_stops_a_running_job(self) -> None:
        service = self._service()
        assets = self._assets()

        def blocking_collect(*_args, **kwargs):
            kwargs["cancel"].wait(2)
            raise AnalysisCancelled()

        with patch("backend.mod_order_analysis.collect_evidence", side_effect=blocking_collect):
            initial = service.start(
                assets,
                {"ai_enabled": False, "language": "en-US"},
                game_id="warhammer3",
                current_order=["a", "b"],
            )
            service.cancel(initial["id"])
            state = _wait(service, initial["id"])
        self.assertEqual(state["status"], "cancelled")

    def test_rejects_empty_assets(self) -> None:
        service = self._service()
        with self.assertRaisesRegex(ValueError, "aiOrder.noMods"):
            service.start([], {"ai_enabled": False}, game_id="warhammer3")

    def test_requires_ai_configuration_when_enabled(self) -> None:
        service = self._service()
        with self.assertRaisesRegex(ValueError, "模型"):
            service.start(
                self._assets(),
                {"ai_enabled": True, "ai_base_url": "https://example.invalid/v1"},
                game_id="warhammer3",
            )

    def test_stale_session_is_rejected(self) -> None:
        service = self._service()
        with self.assertRaisesRegex(ValueError, "aiOrder.staleSession"):
            service.status("missing")
        with self.assertRaisesRegex(ValueError, "aiOrder.staleSession"):
            service.apply("missing")


if __name__ == "__main__":
    unittest.main()
