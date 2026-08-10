"""Silent-drop detector tests.

The v1.11.1 lesson: locosp's server can return HTTP 200 ok:true while
having dropped every record server-side. These tests pin the exact
detection logic."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gungnir.diagnostics import (  # noqa: E402
    check_deliberate_skip,
    check_silent_drop,
    SilentDrop,
)


class SilentDropTests(unittest.TestCase):

    def test_returns_silent_drop_when_all_counters_zero(self):
        """The v4 server bug pattern: 200 + ok:true + every counter zero
        on a non-empty upload. This is the case Muninn v1.11.1 added
        detection for after Badger reported '0 new' silently."""
        resp = {"ok": True, "aircraft_imported": 0, "aircraft_already_seen": 0}
        sd = check_silent_drop(200, resp, sent_count=10)
        self.assertIsInstance(sd, SilentDrop)
        self.assertEqual(sd.sent_count, 10)

    def test_returns_none_when_any_counter_nonzero(self):
        """A single non-zero counter means the upload was at least
        partially accepted. Not a silent drop."""
        resp = {"ok": True, "aircraft_imported": 1, "aircraft_already_seen": 0}
        self.assertIsNone(check_silent_drop(200, resp, sent_count=10))

    def test_returns_none_on_empty_upload(self):
        """A 0-record upload trivially has 0 counters. Not a drop."""
        resp = {"ok": True}
        self.assertIsNone(check_silent_drop(200, resp, sent_count=0))

    def test_returns_none_on_non_200(self):
        """The detector is specifically for the success-shaped failure.
        Real HTTP errors are obvious and out of scope here."""
        resp = {"ok": True}
        self.assertIsNone(check_silent_drop(500, resp, sent_count=10))

    def test_returns_none_when_ok_is_false(self):
        """ok:false is a normal failure shape, not a silent drop."""
        resp = {"ok": False, "error": "rate limited"}
        self.assertIsNone(check_silent_drop(200, resp, sent_count=10))

    def test_forward_compatible_with_unknown_counters(self):
        """The check is 'did ANY known counter come back non-zero',
        unknown new server counters don't trigger a false positive."""
        resp = {"ok": True, "some_future_counter": 99}
        sd = check_silent_drop(200, resp, sent_count=10)
        # All KNOWN counters are zero, so this DOES register as a drop
        #, that's correct behavior; unknown counters can't satisfy us.
        # If the server adds a new meaningful counter, we add it to
        # KNOWN_COUNTERS in diagnostics.py. Explicit list = explicit
        # contract.
        self.assertIsInstance(sd, SilentDrop)



class DeliberateSkipTests(unittest.TestCase):
    """A dedupe reply is the server declining work it already did.

    Observed live 2026-08-10: muninn-push re-sent an unchanged ADS-B snapshot
    on its hourly cadence and every chunk came back 200/ok:true with all
    counters zero plus info "This payload was already uploaded recently - no
    new processing." The silent-drop detector failed the job, sleipnir-health
    alerted, and 43 of 109 health runs over five days were this false alarm.
    """

    DEDUPE = {
        "ok": True, "imported": 0, "updated": 0, "captured": 0,
        "skipped": 0, "cooldown": 0, "cap_hits": 0,
        "aircraft_imported": 0, "aircraft_already_seen": 0,
        "meshcore_imported": 0,
        "info": "This payload was already uploaded recently - no new processing.",
    }

    def test_dedupe_reply_is_not_a_silent_drop(self):
        self.assertIsNone(check_silent_drop(200, self.DEDUPE, sent_count=8))

    def test_dedupe_reply_is_reported_as_a_skip(self):
        self.assertIn("already uploaded recently",
                      check_deliberate_skip(self.DEDUPE))

    def test_skip_detection_is_case_insensitive(self):
        resp = {"ok": True, "info": "ALREADY UPLOADED RECENTLY"}
        self.assertIsNotNone(check_deliberate_skip(resp))

    def test_skip_read_from_message_and_note_fields(self):
        for field in ("message", "note"):
            with self.subTest(field=field):
                resp = {"ok": True, field: "no new processing for this payload"}
                self.assertIsNotNone(check_deliberate_skip(resp))

    def test_unexplained_zero_counters_still_a_silent_drop(self):
        """The regression this must never cause: zero counters with no
        explanation is still the v4-server failure the detector exists for."""
        resp = {"ok": True, "aircraft_imported": 0, "info": "queued"}
        self.assertIsNone(check_deliberate_skip(resp))
        self.assertIsInstance(check_silent_drop(200, resp, sent_count=8),
                              SilentDrop)

    def test_non_string_info_is_ignored(self):
        """A server that returns info as a dict must not crash the check."""
        resp = {"ok": True, "info": {"reason": "already uploaded recently"}}
        self.assertIsNone(check_deliberate_skip(resp))

if __name__ == "__main__":
    unittest.main()
