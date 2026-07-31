"""Direct file-I/O tests for gungnir.hwm.

The transport suite only ever exercises hwm through mocks; these tests
pin the actual on-disk contract external monitors (lab-doctor style
scanners) read: the counters extraction, the timestamp fields, the full
payload preservation, and read()'s None-on-missing behavior.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gungnir import hwm
from gungnir.diagnostics import KNOWN_COUNTERS


class HwmTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        patcher = mock.patch.object(hwm, "config_dir", return_value=self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_record_then_read_round_trip(self) -> None:
        payload = {"ok": True, "aircraft_imported": 4,
                   "aircraft_already_seen": 6, "unknown_counter": 9}
        hwm.record("testtool", payload)
        d = hwm.read("testtool")
        self.assertIsNotNone(d)
        # Known counters land in the structured field...
        self.assertEqual(d["counters"].get("aircraft_imported"), 4)
        self.assertEqual(d["counters"].get("aircraft_already_seen"), 6)
        # ...unknown ones only in the preserved raw payload.
        self.assertNotIn("unknown_counter", d["counters"])
        self.assertEqual(d["last_upload_payload"], payload)

    def test_timestamps_are_fresh_and_consistent(self) -> None:
        before = time.time()
        hwm.record("testtool", {"ok": True})
        d = hwm.read("testtool")
        self.assertGreaterEqual(d["last_upload_ts"], before - 1)
        self.assertLessEqual(d["last_upload_ts"], time.time() + 1)
        self.assertRegex(d["last_upload_iso"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_counters_only_include_keys_present_in_payload(self) -> None:
        # No zero-filling of absent counters: consumers must be able to
        # tell "server didn't return it" from "server returned 0".
        hwm.record("testtool", {"aircraft_imported": 1})
        d = hwm.read("testtool")
        self.assertEqual(list(d["counters"].keys()), ["aircraft_imported"])

    def test_none_counter_values_coerce_to_zero(self) -> None:
        name = next(iter(KNOWN_COUNTERS))
        hwm.record("testtool", {name: None})
        d = hwm.read("testtool")
        self.assertEqual(d["counters"][name], 0)

    def test_read_missing_returns_none(self) -> None:
        self.assertIsNone(hwm.read("testtool"))

    def test_read_corrupt_returns_none(self) -> None:
        (self.dir / "hwm.json").write_text("{not json")
        self.assertIsNone(hwm.read("testtool"))

    def test_record_failure_is_nonfatal(self) -> None:
        # A read-only config dir must not crash the upload path. HWM is
        # monitoring garnish, never load-bearing.
        with mock.patch.object(hwm.Path, "write_text",
                               side_effect=OSError("read-only")):
            hwm.record("testtool", {"ok": True})  # must not raise


if __name__ == "__main__":
    unittest.main()
