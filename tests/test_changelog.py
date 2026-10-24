from __future__ import annotations

import unittest

from backend.changelog import (
    APP_CHANGELOG,
    CHANGELOG_STRUCTURE,
    SUPPORTED_CHANGELOG_LANGUAGES,
    get_all_changelogs,
)


class ChangelogTests(unittest.TestCase):
    def test_default_changelog_starts_with_current_release(self) -> None:
        releases = get_all_changelogs()
        self.assertEqual(releases[0]["version"], "1.3.0")
        self.assertTrue(releases[0]["entries"])
        for release in releases:
            self.assertIn("version", release)
            self.assertIn("date", release)
            for entry in release["entries"]:
                self.assertTrue(entry["title"])
                for change in entry["changes"]:
                    self.assertIn(change["type"], {"feature", "improvement", "optimize", "fix"})
                    self.assertTrue(change["text"])

    def test_all_supported_languages_have_the_same_shape(self) -> None:
        expected_versions = [release["version"] for release in CHANGELOG_STRUCTURE]
        for language in SUPPORTED_CHANGELOG_LANGUAGES:
            with self.subTest(language=language):
                releases = get_all_changelogs(language)
                self.assertEqual([release["version"] for release in releases], expected_versions)
                for release in releases:
                    self.assertTrue(all(entry["title"] for entry in release["entries"]))

    def test_unknown_language_falls_back_to_default(self) -> None:
        self.assertEqual(get_all_changelogs("xx-XX"), get_all_changelogs("zh-CN"))

    def test_app_changelog_is_the_default_catalog(self) -> None:
        self.assertEqual(APP_CHANGELOG, get_all_changelogs("zh-CN"))

    def test_returned_catalog_is_isolated(self) -> None:
        first = get_all_changelogs("en-US")
        first[0]["entries"][0]["title"] = "mutated"
        second = get_all_changelogs("en-US")
        self.assertNotEqual(second[0]["entries"][0]["title"], "mutated")


if __name__ == "__main__":
    unittest.main()
