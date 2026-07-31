"""Transport behavior tests.

Mocks urllib.request.urlopen to exercise the retry path, the 429-bail
path, the one-slot contract on send(), the inter-chunk cooldown, and
the Client validation rules. The Muninn parity test (envelope bytes)
covers the happy path, these tests cover the harder edges.

To skip backoff sleeps, we mock ``gungnir.transport.time.sleep``; the
library itself has no test-only knob in its public signature.
"""
from __future__ import annotations

import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gungnir  # noqa: E402
from gungnir import transport  # noqa: E402
from gungnir.envelope import build_payload  # noqa: E402


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    """Construct a realistic HTTPError. urllib's HTTPError implements .read()
    via the file-like third positional, so test mocks must pass one."""
    return urllib.error.HTTPError(
        url="https://example.invalid/api/upload/",
        code=code,
        msg="error",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def _http_ok(body: bytes) -> mock.MagicMock:
    """Build a context-manager-shaped mock that mimics urlopen()'s return."""
    cm = mock.MagicMock()
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.status = 200
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


# ── User-Agent helper ─────────────────────────────────────────────────────

class UserAgentTests(unittest.TestCase):
    """The _user_agent helper is module-private but worth pinning, it
    encodes the bot-UA contract every external call follows."""

    def test_bare_form(self):
        self.assertEqual(
            transport._user_agent("muninn", "1.11.1"),
            "muninn/1.11.1",
        )

    def test_with_extra_appends_url(self):
        self.assertEqual(
            transport._user_agent("muninn", "1.11.1",
                                  "https://github.com/HiroAlleyCat/adsb-to-wdgwars"),
            "muninn/1.11.1 (+https://github.com/HiroAlleyCat/adsb-to-wdgwars)",
        )

    def test_with_empty_extra_uses_bare_form(self):
        self.assertEqual(transport._user_agent("muninn", "1", ""), "muninn/1")


# ── send_chunk retry/backoff path ─────────────────────────────────────────

class SendChunkRetryTests(unittest.TestCase):

    def test_retries_on_500_then_succeeds(self):
        ok = b'{"ok": true, "aircraft_imported": 1}'
        urlopen = mock.MagicMock(side_effect=[
            _http_error(500, b'{"error":"boom"}'),
            _http_error(500, b'{"error":"boom"}'),
            _http_ok(ok),
        ])
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.transport.time.sleep") as sleep:
            rc, data = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1, backoff_base=2.0,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(data, {"ok": True, "aircraft_imported": 1})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 4.0])

    def test_gives_up_after_max_attempts_on_persistent_5xx(self):
        urlopen = mock.MagicMock(side_effect=[
            _http_error(502, b"") for _ in range(3)
        ])
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.transport.time.sleep") as sleep:
            rc, _ = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1, max_attempts=3, backoff_base=2.0,
            )
        self.assertEqual(rc, 1)
        # 3 attempts → 2 sleeps (no sleep after the final attempt).
        self.assertEqual(len(sleep.call_args_list), 2)

    def test_does_not_retry_on_400(self):
        urlopen = mock.MagicMock(side_effect=[_http_error(400, b'{"error":"bad"}')])
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.transport.time.sleep") as sleep:
            rc, _ = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1, max_attempts=3,
            )
        self.assertEqual(rc, 1)
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_429_raises_batch_aborted_with_cooldown(self):
        urlopen = mock.MagicMock(side_effect=[
            _http_error(429, b'{"retry_after": 90}'),
        ])
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.record") as cooldown_record:
            with self.assertRaises(transport.BatchAborted) as ctx:
                transport.send_chunk(
                    "muninn", "1.11.1",
                    "https://example.invalid/api/upload/",
                    "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                    sent_count=1, max_attempts=3,
                )
        self.assertEqual(ctx.exception.retry_after, 90.0)
        cooldown_record.assert_called_once_with("muninn", 90.0)

    def test_413_payload_too_large_records_cooldown_and_returns_envelope(self):
        """LOCOSP rolled out a 15 MB body cap with a structured 413 envelope
        on 2026-06-05. Treat it like 429 (record a cooldown, don't retry),
        but return rc=1 instead of raising BatchAborted. The caller may
        have other queued payloads to attempt."""
        envelope = (
            b'{"ok":false,"error":"payload-too-large","http_status":413,'
            b'"max_bytes":15728640,"received":31457480,"retry_after":0}'
        )
        urlopen = mock.MagicMock(side_effect=[_http_error(413, envelope)])
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.record") as cooldown_record, \
             mock.patch("gungnir.transport.time.sleep") as sleep:
            rc, data = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1, max_attempts=3,
            )
        self.assertEqual(rc, 1)
        self.assertEqual(data["error"], "payload-too-large")
        self.assertEqual(data["max_bytes"], 15728640)
        self.assertEqual(data["received"], 31457480)
        # Cooldown recorded, default 30s when retry_after is 0/missing.
        cooldown_record.assert_called_once()
        self.assertEqual(cooldown_record.call_args.args[0], "muninn")
        self.assertGreaterEqual(cooldown_record.call_args.args[1], 30.0)
        # Single attempt only. Must not retry the 413.
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_413_honors_server_retry_after_when_provided(self):
        """If LOCOSP populates retry_after on the 413 envelope later, the
        client must honor it instead of the 30s default."""
        envelope = (
            b'{"ok":false,"error":"payload-too-large","http_status":413,'
            b'"max_bytes":15728640,"received":20000000,"retry_after":120}'
        )
        urlopen = mock.MagicMock(side_effect=[_http_error(413, envelope)])
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.record") as cooldown_record:
            rc, _ = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1, max_attempts=3,
            )
        self.assertEqual(rc, 1)
        cooldown_record.assert_called_once_with("muninn", 120.0)

    def test_413_without_envelope_falls_through_to_generic_rejected(self):
        """A 413 from CF or any non-LOCOSP layer (no payload-too-large body)
        must not get the structured treatment. It should hit the generic
        rejected branch like any other 4xx, no cooldown recorded."""
        urlopen = mock.MagicMock(side_effect=[_http_error(413, b"<html>nope</html>")])
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.record") as cooldown_record:
            rc, _ = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1, max_attempts=3,
            )
        self.assertEqual(rc, 1)
        cooldown_record.assert_not_called()

    def test_silent_drop_returns_rc1_not_batch_aborted(self):
        """Silent drop is per-chunk. The next chunk might succeed."""
        ok_silent = b'{"ok": true, "aircraft_imported": 0, "aircraft_already_seen": 0}'
        urlopen = mock.MagicMock(return_value=_http_ok(ok_silent))
        with mock.patch("urllib.request.urlopen", urlopen):
            rc, _ = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "longkeyabc123xyz789longenough",
                build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1,
            )
        self.assertEqual(rc, 1)

    def test_dry_run_does_not_call_urlopen(self):
        urlopen = mock.MagicMock()
        with mock.patch("urllib.request.urlopen", urlopen):
            rc, data = transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "k", build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1, dry_run=True,
            )
        self.assertEqual(rc, 0)
        self.assertTrue(data["dry_run"])
        urlopen.assert_not_called()

    def test_user_agent_extra_appears_in_header(self):
        """The +URL convention must reach the actual outbound request."""
        urlopen = mock.MagicMock(return_value=_http_ok(b'{"ok": true, "aircraft_imported": 1}'))
        with mock.patch("urllib.request.urlopen", urlopen):
            transport.send_chunk(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/",
                "longkeyabc123xyz789longenough",
                build_payload(aircraft=[{"icao": "AAAAAA"}]),
                sent_count=1,
                user_agent_extra="https://github.com/HiroAlleyCat/adsb-to-wdgwars",
            )
        sent_req = urlopen.call_args.args[0]
        ua = sent_req.headers.get("User-agent")
        self.assertIn("muninn/1.11.1", ua)
        self.assertIn("(+https://github.com/HiroAlleyCat/adsb-to-wdgwars)", ua)


# ── send() contract + chunk cooldown ──────────────────────────────────────

class SendOneSlotContractTests(unittest.TestCase):

    def test_zero_slots_raises(self):
        with self.assertRaises(ValueError) as ctx:
            transport.send(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/", "k",
            )
        self.assertIn("exactly one", str(ctx.exception))

    def test_two_slots_raises(self):
        with self.assertRaises(ValueError) as ctx:
            transport.send(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/", "k",
                aircraft=[{"icao": "A"}],
                networks=[{"bssid": "B"}],
            )
        msg = str(ctx.exception)
        self.assertIn("aircraft", msg)
        self.assertIn("networks", msg)

    def test_empty_slot_is_noop_returns_zero(self):
        rc = transport.send(
            "muninn", "1.11.1",
            "https://example.invalid/api/upload/", "k",
            aircraft=[],
        )
        self.assertEqual(rc, 0)

    def test_429_aborts_remaining_chunks(self):
        urlopen = mock.MagicMock(side_effect=[
            _http_error(429, b'{"retry_after": 60}'),
        ])
        records = [{"icao": f"{i:06X}"} for i in range(1500)]
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.check_and_sleep"), \
             mock.patch("gungnir.cooldown.record"):
            rc = transport.send(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/", "k",
                aircraft=records, batch_size=500, max_attempts=1,
                chunk_cooldown=0,
            )
        self.assertEqual(rc, 1)
        self.assertEqual(urlopen.call_count, 1)


class ChunkCooldownTests(unittest.TestCase):
    """N chunks → N-1 inter-chunk sleeps. No sleep after the last chunk."""

    def test_three_chunks_two_inter_chunk_sleeps(self):
        ok = b'{"ok": true, "aircraft_imported": 1, "aircraft_already_seen": 0}'
        urlopen = mock.MagicMock(side_effect=[_http_ok(ok) for _ in range(3)])
        records = [{"icao": f"{i:06X}"} for i in range(3)]
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.check_and_sleep"), \
             mock.patch("gungnir.hwm.record"), \
             mock.patch("gungnir.transport.time.sleep") as sleep:
            rc = transport.send(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/", "k",
                aircraft=records, batch_size=1,  # force 3 chunks
                chunk_cooldown=2.5,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(urlopen.call_count, 3)
        # 3 chunks → 2 inter-chunk cooldowns (none after the last).
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.5, 2.5])

    def test_chunk_cooldown_zero_disables_sleep(self):
        ok = b'{"ok": true, "aircraft_imported": 1, "aircraft_already_seen": 0}'
        urlopen = mock.MagicMock(side_effect=[_http_ok(ok) for _ in range(3)])
        records = [{"icao": f"{i:06X}"} for i in range(3)]
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.check_and_sleep"), \
             mock.patch("gungnir.hwm.record"), \
             mock.patch("gungnir.transport.time.sleep") as sleep:
            transport.send(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/", "k",
                aircraft=records, batch_size=1, chunk_cooldown=0,
            )
        sleep.assert_not_called()

    def test_single_chunk_no_inter_chunk_sleep(self):
        ok = b'{"ok": true, "aircraft_imported": 1, "aircraft_already_seen": 0}'
        urlopen = mock.MagicMock(return_value=_http_ok(ok))
        with mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("gungnir.cooldown.check_and_sleep"), \
             mock.patch("gungnir.hwm.record"), \
             mock.patch("gungnir.transport.time.sleep") as sleep:
            transport.send(
                "muninn", "1.11.1",
                "https://example.invalid/api/upload/", "k",
                aircraft=[{"icao": "AAAAAA"}], chunk_cooldown=5.0,
            )
        sleep.assert_not_called()


# ── Whoami timeout ────────────────────────────────────────────────────────

class WhoamiTests(unittest.TestCase):

    def test_whoami_does_not_clamp_caller_timeout(self):
        """Regression: previously whoami clamped to 30s silently. Now it
        passes the caller's timeout through unchanged."""
        urlopen = mock.MagicMock(return_value=_http_ok(
            b'{"ok": true, "username": "test"}',
        ))
        with mock.patch("urllib.request.urlopen", urlopen):
            transport.whoami(
                "muninn", "1.11.1",
                "https://example.invalid/api/me",
                "k", timeout=180.0,
            )
        kwargs = urlopen.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 180.0)


# ── Client validation + plumbing ──────────────────────────────────────────

class ClientTests(unittest.TestCase):

    def test_client_requires_tool(self):
        with self.assertRaises(ValueError):
            gungnir.Client(tool="", version="1.0.0")

    def test_client_requires_version(self):
        with self.assertRaises(ValueError):
            gungnir.Client(tool="muninn", version="")

    def test_client_rejects_zero_max_attempts(self):
        with self.assertRaises(ValueError):
            gungnir.Client(tool="muninn", version="1.0", max_attempts=0)

    def test_client_rejects_negative_chunk_cooldown(self):
        with self.assertRaises(ValueError):
            gungnir.Client(tool="muninn", version="1.0", chunk_cooldown=-1)

    def test_client_rejects_zero_timeout(self):
        with self.assertRaises(ValueError):
            gungnir.Client(tool="muninn", version="1.0", timeout=0)

    def test_client_rejects_path_traversal_in_tool_name(self):
        for bad in ("../etc", "..\\evil", "foo/bar", "foo\\bar", "with\x00null", ".."):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    gungnir.Client(tool=bad, version="1.0")

    def test_client_send_threads_timeout_override(self):
        client = gungnir.Client(tool="muninn", version="1.0", timeout=120)
        with mock.patch("gungnir.transport.send") as send:
            send.return_value = 0
            client.send("k", aircraft=[], timeout=5)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 5.0)

    def test_client_send_uses_default_timeout_when_unset(self):
        client = gungnir.Client(tool="muninn", version="1.0", timeout=77)
        with mock.patch("gungnir.transport.send") as send:
            send.return_value = 0
            client.send("k", aircraft=[])
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 77.0)

    def test_client_send_threads_chunk_cooldown(self):
        client = gungnir.Client(tool="muninn", version="1.0", chunk_cooldown=3.5)
        with mock.patch("gungnir.transport.send") as send:
            send.return_value = 0
            client.send("k", aircraft=[])
        self.assertEqual(send.call_args.kwargs["chunk_cooldown"], 3.5)

    def test_client_send_threads_user_agent_extra(self):
        client = gungnir.Client(
            tool="muninn", version="1.0",
            user_agent_extra="https://github.com/HiroAlleyCat/adsb-to-wdgwars",
        )
        with mock.patch("gungnir.transport.send") as send:
            send.return_value = 0
            client.send("k", aircraft=[])
        self.assertEqual(
            send.call_args.kwargs["user_agent_extra"],
            "https://github.com/HiroAlleyCat/adsb-to-wdgwars",
        )

    def test_client_whoami_uses_whoami_timeout_not_send_timeout(self):
        """Client.whoami() must honor whoami_timeout, not get clamped to
        an arbitrary value and not borrow the send timeout."""
        client = gungnir.Client(
            tool="muninn", version="1.0",
            timeout=120, whoami_timeout=45,
        )
        with mock.patch("gungnir.transport.whoami") as whoami:
            whoami.return_value = 0
            client.whoami("k")
        self.assertEqual(whoami.call_args.kwargs["timeout"], 45.0)

    def test_client_repr_includes_tool_and_version(self):
        client = gungnir.Client(tool="muninn", version="1.11.1")
        r = repr(client)
        self.assertIn("muninn", r)
        self.assertIn("1.11.1", r)


if __name__ == "__main__":
    unittest.main()
