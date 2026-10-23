from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.api import _explorer_select_command


class ExplorerSelectCommandTests(unittest.TestCase):
    def test_quotes_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "with space" / "mod.pack"
            command = _explorer_select_command(path)
            self.assertTrue(command.startswith("explorer.exe /select,"))
            self.assertIn(str(path.resolve(strict=False)), command)
            self.assertIn('"', command)

    def test_plain_path_is_still_quoted_by_list2cmdline_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mod.pack"
            command = _explorer_select_command(path)
            self.assertTrue(command.startswith("explorer.exe /select,"))
            self.assertIn(str(path.resolve(strict=False)), command)

    def test_rejects_embedded_quote(self) -> None:
        with self.assertRaisesRegex(ValueError, "引号"):
            _explorer_select_command(Path('bad"name.pack'))


if __name__ == "__main__":
    unittest.main()
