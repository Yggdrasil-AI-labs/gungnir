"""Direct file-I/O tests for gungnir.cooldown.

The transport suite only ever exercises cooldown through mocks; these
tests pin the actual persistence behavior: the 900 s sleep cap, the
record-then-respect round trip, clear-on-zero, and the silent no-op on
missing/corrupt state.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gungnir import cooldown


class CooldownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        patcher = mock.patch.object(
            cooldown, "config_dir", return_value=self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _file(self) -> Path:
        return self.dir / "cooldown.json"

    def test_record_persists_a_future_deadline(self) -> None:
        cooldown.record("testtool", 30)
        d = json.loads(self._file().read_text())
        self.assertGreater(d["until"], time.time() + 25)
        self.assertLess(d["until"], time.time() + 35)

    def test_record_zero_clears_the_file(self) -> None:
        cooldown.record("testtool", 30)
        self.assertTrue(self._file().exists())
        cooldown.record("testtool", 0)
        self.assertFalse(self._file().exists())

    def test_record_negative_clears_the_file(self) -> None:
        cooldown.record("testtool", 30)
        cooldown.record("testtool", -5)
        self.assertFalse(self._file().exists())

    def test_check_and_sleep_sleeps_remaining_time(self) -> None:
        cooldown.record("testtool", 60)
        with mock.patch.object(cooldown.time, "sleep") as slept:
            cooldown.check_and_sleep("testtool")
        slept.assert_called_once()
        waited = slept.call_args.args[0]
        self.assertGreater(waited, 55)
        self.assertLessEqual(waited, 60)

    def test_sleep_capped_at_900s_by_default(self) -> None:
        # A stuck/garbage deadline far in the future must not deadlock an
        # automated runner: the wait is clamped to 15 minutes.
        self._file().write_text(
            json.dumps({"until": time.time() + 86400}))
        with mock.patch.object(cooldown.time, "sleep") as slept:
            cooldown.check_and_sleep("testtool")
        slept.assert_called_once_with(900)

    def test_custom_cap_is_honored(self) -> None:
        self._file().write_text(
            json.dumps({"until": time.time() + 86400}))
        with mock.patch.object(cooldown.time, "sleep") as slept:
            cooldown.check_and_sleep("testtool", cap_seconds=10)
        slept.assert_called_once_with(10)

    def test_expired_deadline_does_not_sleep(self) -> None:
        self._file().write_text(
            json.dumps({"until": time.time() - 5}))
        with mock.patch.object(cooldown.time, "sleep") as slept:
            cooldown.check_and_sleep("testtool")
        slept.assert_not_called()

    def test_missing_file_is_a_silent_noop(self) -> None:
        with mock.patch.object(cooldown.time, "sleep") as slept:
            cooldown.check_and_sleep("testtool")
        slept.assert_not_called()

    def test_corrupt_file_is_a_silent_noop(self) -> None:
        self._file().write_text("{not json")
        with mock.patch.object(cooldown.time, "sleep") as slept:
            cooldown.check_and_sleep("testtool")
        slept.assert_not_called()


if __name__ == "__main__":
    unittest.main()
