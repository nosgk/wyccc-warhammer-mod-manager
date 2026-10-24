from __future__ import annotations

import unittest

from backend.secrets import PREFIX, is_protected, is_supported, protect, unprotect


class SecretProtectionTests(unittest.TestCase):
    def test_plain_values_are_unchanged(self) -> None:
        self.assertEqual(unprotect("plain-secret"), "plain-secret")
        self.assertFalse(is_protected("plain-secret"))

    @unittest.skipUnless(is_supported(), "DPAPI is Windows-only")
    def test_round_trip_on_windows(self) -> None:
        protected = protect("sk-test-value")
        self.assertTrue(protected.startswith(PREFIX))
        self.assertTrue(is_protected(protected))
        self.assertEqual(unprotect(protected), "sk-test-value")

    @unittest.skipUnless(is_supported(), "DPAPI is Windows-only")
    def test_already_protected_value_is_not_double_wrapped(self) -> None:
        protected = protect("sk-test-value")
        self.assertEqual(protect(protected), protected)

    @unittest.skipIf(is_supported(), "non-Windows fallback")
    def test_non_windows_keeps_plain_value(self) -> None:
        self.assertEqual(protect("sk-test-value"), "sk-test-value")


if __name__ == "__main__":
    unittest.main()
