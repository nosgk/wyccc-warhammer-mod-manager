from __future__ import annotations

import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import main


class LoggingTests(unittest.TestCase):
    def test_configure_logging_uses_rotating_file_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("main.logging.basicConfig") as basic:
            root = Path(temporary)
            main.configure_logging(root)

            handlers = basic.call_args.kwargs["handlers"]
            rotating = [handler for handler in handlers if isinstance(handler, RotatingFileHandler)]
            self.assertEqual(len(rotating), 1)
            self.assertEqual(rotating[0].maxBytes, main.LOG_MAX_BYTES)
            self.assertEqual(rotating[0].backupCount, main.LOG_BACKUP_COUNT)
            self.assertTrue((root / "logs" / "app.log").exists())
            for handler in handlers:
                handler.close()


if __name__ == "__main__":
    unittest.main()
