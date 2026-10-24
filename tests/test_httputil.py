from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request
from unittest.mock import patch

from backend.httputil import request_bytes, request_json


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.payload


class HttpRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = Request("https://example.invalid/api")

    def test_retries_transient_http_error_then_succeeds(self) -> None:
        error = HTTPError("https://example.invalid/api", 503, "busy", {}, None)
        with patch(
            "backend.httputil.urlopen", side_effect=[error, _Response(b"ok")]
        ) as urlopen:
            self.assertEqual(request_bytes(self.request, retries=2, backoff=0), b"ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_does_not_retry_definitive_client_error(self) -> None:
        error = HTTPError("https://example.invalid/api", 404, "missing", {}, None)
        with patch("backend.httputil.urlopen", side_effect=error) as urlopen:
            with self.assertRaises(HTTPError):
                request_bytes(self.request, retries=3, backoff=0)
        self.assertEqual(urlopen.call_count, 1)

    def test_retries_network_error_then_succeeds(self) -> None:
        with patch(
            "backend.httputil.urlopen",
            side_effect=[URLError("offline"), _Response(b"ok")],
        ) as urlopen:
            self.assertEqual(request_bytes(self.request, retries=1, backoff=0), b"ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_raises_after_retry_budget(self) -> None:
        with patch(
            "backend.httputil.urlopen", side_effect=URLError("offline")
        ) as urlopen:
            with self.assertRaises(URLError):
                request_bytes(self.request, retries=2, backoff=0)
        self.assertEqual(urlopen.call_count, 3)

    def test_request_json_decodes_payload(self) -> None:
        with patch(
            "backend.httputil.urlopen",
            return_value=_Response(json.dumps({"ok": True}).encode("utf-8")),
        ):
            self.assertEqual(request_json(self.request, retries=0), {"ok": True})


if __name__ == "__main__":
    unittest.main()
