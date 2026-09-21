"""Shared already-sent holds.

Ported from the implementation proved in Muninn v2.3.0-2.4.0 and generalised
to the other two slots. The history is worth keeping because each rule here
is a bug that shipped:

- v2.3.0 recorded a payload as sent only once the server's counters accounted
  for all of it. They routinely do not: measured on a live feeder, three of
  six consecutive cycles came back one short, and the missing record is
  counted nowhere at all. The gate never engaged once for that operator.
- v2.3.1 held everything for an hour. On a fixed station that does almost
  nothing, because records turn over faster than the hold expires while the
  payload is still full of things the server has had for days.
- v2.4.0 added the confirmed hold, and its first test of "a later short mark
  must not shorten a long hold" was vacuous: the second upload was skipped
  entirely, so the path it claimed to cover never ran.

Run: python -m pytest tests/test_holds.py
"""
from __future__ import annotations

import json
import time

import pytest

from gungnir import holds


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """No test may read or write the operator's real config dir."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def ac(icao):
    return {"icao": icao, "lat": 1.0, "lon": 2.0}


class TestIdentity:
    def test_each_slot_has_its_own_key(self):
        assert holds.identity({"icao": "abc123"}, "aircraft") == "ABC123"
        assert holds.identity({"bssid": "aa:bb:cc"}, "networks") == "AA:BB:CC"
        assert holds.identity({"node_id": "deadbeef"},
                              "meshcore_nodes") == "DEADBEEF"

    def test_networks_accepts_the_field_aliases_in_use(self):
        assert holds.identity({"netid": "aa:bb"}, "networks") == "AA:BB"
        assert holds.identity({"mac": "aa:bb"}, "networks") == "AA:BB"

    def test_unreadable_identity_is_none_not_a_guess(self):
        # None means "always upload". Dropping an observation over a
        # bookkeeping field is worse than sending it twice.
        assert holds.identity({}, "aircraft") is None
        assert holds.identity({"icao": ""}, "aircraft") is None
        assert holds.identity({"icao": "abc"}, "unknown_slot") is None


class TestHoldLifecycle:
    def test_held_records_are_filtered_out(self):
        now = time.time()
        holds.record_sent("t", [ac("ABC123")], "aircraft", now)
        state = holds.load("t")
        assert holds.unheld([ac("ABC123")], "aircraft", state, now) == []

    def test_an_unheld_record_survives_the_filter(self):
        now = time.time()
        holds.record_sent("t", [ac("ABC123")], "aircraft", now)
        state = holds.load("t")
        assert len(holds.unheld([ac("ABC123"), ac("DEF456")],
                                "aircraft", state, now)) == 1

    def test_a_record_with_no_identity_is_never_held(self):
        now = time.time()
        rec = {"lat": 1.0, "lon": 2.0}
        holds.record_sent("t", [rec], "aircraft", now)
        assert holds.unheld([rec], "aircraft", holds.load("t"), now) == [rec]

    def test_holds_expire(self):
        now = time.time()
        holds.record_sent("t", [ac("ABC123")], "aircraft", now,
                          ttl=holds.SENT_TTL)
        later = now + holds.SENT_TTL + 1
        state = holds.prune(holds.load("t"), later)
        assert state == {}
        assert len(holds.unheld([ac("ABC123")], "aircraft", state, later)) == 1

    def test_confirmed_hold_outlives_the_short_one(self):
        now = time.time()
        holds.record_sent("t", [ac("ABC123")], "aircraft", now,
                          ttl=holds.CONFIRMED_TTL)
        two_hours = now + 2 * holds.SENT_TTL
        state = holds.prune(holds.load("t"), two_hours)
        assert holds.unheld([ac("ABC123")], "aircraft", state, two_hours) == []

        past_day = now + holds.CONFIRMED_TTL + 1
        state = holds.prune(holds.load("t"), past_day)
        assert len(holds.unheld([ac("ABC123")], "aircraft", state,
                                past_day)) == 1, "even the long hold expires"

    def test_a_short_mark_never_shortens_a_long_hold(self):
        now = time.time()
        holds.record_sent("t", [ac("ABC123")], "aircraft", now,
                          ttl=holds.CONFIRMED_TTL)
        holds.record_sent("t", [ac("ABC123"), ac("DEF456")], "aircraft", now,
                          ttl=holds.SENT_TTL)
        state = holds.load("t")
        assert state["ABC123"] > now + holds.SENT_TTL
        assert state["DEF456"] <= now + holds.SENT_TTL

    def test_tools_do_not_share_state(self):
        now = time.time()
        holds.record_sent("tool-a", [ac("ABC123")], "aircraft", now)
        assert holds.load("tool-b") == {}


class TestTtlChoice:
    def test_zero_imported_earns_the_confirmed_hold(self):
        assert holds.ttl_for(0) == holds.CONFIRMED_TTL

    def test_anything_imported_keeps_the_short_hold(self):
        # A mixed response does not say WHICH records were new.
        assert holds.ttl_for(1) == holds.SENT_TTL
        assert holds.ttl_for(500) == holds.SENT_TTL

    def test_unknown_is_not_zero(self):
        # The bug this exists to prevent: an unread counter parsed as 0
        # would earn a day-long hold on a payload nobody confirmed.
        assert holds.ttl_for(None) == holds.SENT_TTL


class TestDurability:
    def test_unreadable_state_degrades_to_uploading(self, isolated_config):
        holds.save("t", {"ABC123": time.time() + 999})
        holds._path("t").write_text("{not json")
        assert holds.load("t") == {}

    def test_wrongly_typed_state_degrades_to_uploading(self, isolated_config):
        # Valid JSON, unusable values. Left in place these would reach the
        # arithmetic in prune() and take the upload down with them.
        for bad in ('{"ABC123": "yesterday"}', '["ABC123"]', '"nope"'):
            holds._path("t").parent.mkdir(parents=True, exist_ok=True)
            holds._path("t").write_text(bad)
            assert holds.load("t") == {}

    def test_write_is_atomic_and_leaves_no_temp_file(self, isolated_config,
                                                     monkeypatch):
        # Asserting only that no temp file survives passes against a direct
        # write too, so watch where the bytes actually land: a reader must
        # never be able to observe a half-written map.
        from pathlib import Path as _P
        written = []
        real = _P.write_text

        def spy(self, *a, **kw):
            written.append(_P(self).name)
            return real(self, *a, **kw)

        monkeypatch.setattr(_P, "write_text", spy)
        holds.save("t", {"ABC123": time.time() + 999})
        monkeypatch.undo()

        assert written and written[0].endswith(".tmp"), (
            f"expected the write to land on a temp path, got {written}")
        assert holds.load("t")
        assert list(holds._path("t").parent.glob("*.tmp")) == []

    def test_an_unwritable_config_dir_does_not_raise(self, monkeypatch):
        def boom(*a, **kw):
            raise OSError("read-only")
        monkeypatch.setattr("pathlib.Path.mkdir", boom)
        holds.save("t", {"ABC123": 1.0})  # must not raise

    def test_a_send_time_state_file_reads_as_expired(self, isolated_config):
        # Muninn v2.3.x stored send times in this map. Read as expiry times
        # they are all in the past, so they prune away: one redundant
        # upload, then correct. The reverse would be the dangerous one.
        now = time.time()
        holds._path("t").parent.mkdir(parents=True, exist_ok=True)
        holds._path("t").write_text(json.dumps({"ABC123": now}))
        assert holds.prune(holds.load("t"), now) == {}
