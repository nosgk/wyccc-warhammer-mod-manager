from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.crash_diagnostics import (
    diagnose_launch,
    dump_belongs_to_launch,
    dump_snapshot,
    pack_basename,
    parse_launch_list,
    read_dump_exception,
    read_dump_process,
    read_text,
)
from tests.helpers import write_minidump


class TextAndListTests(unittest.TestCase):
    def test_pack_basename_handles_both_separators(self) -> None:
        self.assertEqual(pack_basename(r"data\mods\a.pack"), "a.pack")
        self.assertEqual(pack_basename("data/mods/a.pack"), "a.pack")
        self.assertEqual(pack_basename("  a.pack  "), "a.pack")

    def test_parse_launch_list_reads_directories_and_mods(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "used_mods.txt"
            path.write_text(
                'add_working_directory "C:/mods";\r\nmod "a.pack";\r\nmod "b.pack";\r\n',
                encoding="utf-8",
            )
            directories, mods = parse_launch_list(path)
            self.assertEqual(directories, ["C:/mods"])
            self.assertEqual(mods, ["a.pack", "b.pack"])

    def test_read_text_supports_utf8_and_utf16(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            utf8 = root / "utf8.txt"
            utf8.write_text("hello", encoding="utf-8")
            self.assertEqual(read_text(utf8), "hello")
            utf16 = root / "utf16.txt"
            utf16.write_bytes("hello".encode("utf-16"))
            self.assertEqual(read_text(utf16), "hello")


class DumpTests(unittest.TestCase):
    def test_reads_exception_and_process_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_minidump(Path(temporary) / "crash.mdmp", pid=1234, created_at=100)
            exception = read_dump_exception(path)
            process = read_dump_process(path)
            self.assertEqual(exception["code"], "0xC0000005")
            self.assertEqual(process, {"pid": 1234, "created_at": 100})

    def test_dump_ownership_requires_matching_pid_and_time(self) -> None:
        self.assertTrue(
            dump_belongs_to_launch({"process": {"pid": 1, "created_at": 100}}, {1}, 50)
        )
        self.assertFalse(
            dump_belongs_to_launch({"process": {"pid": 2, "created_at": 100}}, {1}, 50)
        )
        self.assertFalse(
            dump_belongs_to_launch({"process": {"pid": 1, "created_at": 10}}, {1}, 50)
        )

    def test_snapshot_lists_mdmp_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_minidump(root / "a.mdmp", pid=1)
            (root / "ignore.txt").write_text("x", encoding="utf-8")
            snapshot = dump_snapshot(root)
            self.assertEqual(len(snapshot), 1)


class DiagnoseLaunchTests(unittest.TestCase):
    def test_empty_or_running_record_short_circuits(self) -> None:
        self.assertEqual(diagnose_launch({})["status"], "no_run")
        self.assertEqual(diagnose_launch({}, running=True)["status"], "running")

    def test_interrupted_launch_reports_pending_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logs = root / "logs"
            logs.mkdir()
            (logs / "modified.log").write_text("Mod: a.pack\n", encoding="utf-8")
            a = root / "a.pack"
            b = root / "b.pack"
            a.write_bytes(b"a")
            b.write_bytes(b"b")
            record = {
                "user_dir": str(root),
                "started_at": 0,
                "packs": [
                    {"pack_name": "a.pack", "path": str(a), "pack_type": "mod"},
                    {"pack_name": "b.pack", "path": str(b), "pack_type": "mod"},
                ],
                "dumps_before": {},
            }
            result = diagnose_launch(record)
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["loaded"], ["a.pack"])
            self.assertEqual([row["pack_name"] for row in result["pending"]], ["b.pack"])

    def test_new_crash_dump_marks_the_launch_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "logs").mkdir()
            (root / "logs" / "modified.log").write_text("Mod: a.pack\n", encoding="utf-8")
            crash = root / "crash_report"
            crash.mkdir()
            write_minidump(crash / f"D{time.strftime('%Y-%m-%d')}_T00-00-00.mdmp", pid=1)
            record = {
                "user_dir": str(root),
                "started_at": 0,
                "packs": [{"pack_name": "a.pack", "path": str(root / "a.pack"), "pack_type": "mod"}],
                "dumps_before": {},
            }
            (root / "a.pack").write_bytes(b"a")
            result = diagnose_launch(record)
            self.assertTrue(result["crashed"])
            self.assertTrue(result["should_notify"])


if __name__ == "__main__":
    unittest.main()
