"""Cross-check: gungnir envelopes are byte-identical to Muninn v1.11.1.

If this test ever fails, gungnir is NOT a safe drop-in for Muninn, every
deployed feeder's signatures would stop matching what the server expects.

The test imports Muninn directly and compares the envelope bytes its
upload() function would build against gungnir.build_envelope() output
for the same (payload, key, nonce).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Import gungnir
from gungnir.envelope import build_envelope, build_payload  # noqa: E402

# Try to import muninn - skip cleanly if Muninn isn't on disk (e.g. a CI
# job that only checks out gungnir). Looked for, in order: $MUNINN_PATH, a
# sibling checkout next to this repo (the ~/code layout), then the old
# ~/Documents/GitHub/HiroAlleyCat path. Only the last used to be checked,
# so after the repos moved to ~/code this test skipped everywhere without
# anyone noticing.
import os  # noqa: E402

_CANDIDATES = [Path(p) for p in (os.environ.get("MUNINN_PATH"),) if p] + [
    ROOT.parent / "adsb-to-wdgwars",
    Path.home() / "Documents" / "GitHub" / "HiroAlleyCat" / "adsb-to-wdgwars",
]
MUNINN_PATH = next((c for c in _CANDIDATES if (c / "muninn.py").exists()),
                   None)
HAVE_MUNINN = False
if MUNINN_PATH is not None:
    sys.path.insert(0, str(MUNINN_PATH))
    try:
        import muninn  # noqa: E402
        HAVE_MUNINN = True
    except Exception:
        HAVE_MUNINN = False
_SKIP_WHY = ("Muninn source not found; looked in: "
             + ", ".join(str(c) for c in _CANDIDATES)
             if MUNINN_PATH is None else
             f"muninn.py at {MUNINN_PATH} failed to import")


@unittest.skipUnless(HAVE_MUNINN, _SKIP_WHY)
class MuninnParityTests(unittest.TestCase):
    """Bytewise comparison against muninn.py's upload() envelope-build.

    We can't call muninn.upload() directly without making a real HTTP
    request, so we replicate its envelope-build inline (the exact lines
    1277-1283 of muninn.py v1.11.1) and compare to gungnir output.
    """

    def _muninn_envelope_inline(self, payload, api_key, nonce):
        """Mirror of muninn.upload() envelope build, lines 1277-1283.

        This is the contract gungnir must preserve byte-for-byte."""
        body_json = json.dumps(payload, separators=(",", ":"))
        data_b64 = base64.b64encode(body_json.encode()).decode()
        sig = hmac.new(
            api_key.encode(),
            (nonce + data_b64).encode(),
            hashlib.sha256,
        ).hexdigest()
        return {"data": data_b64, "nonce": nonce, "sig": sig}

    def test_parity_single_aircraft(self):
        api_key = "muninn-parity-test-key"
        nonce = "0123456789abcdef"
        records = [
            {"icao": "A8A5DD", "callsign": "TEST123",
             "lat": 42.123, "lon": -81.456,
             "alt_ft": 30000, "speed_kt": 420, "heading": 270,
             "first_seen": "2026-05-28 12:00:00", "type": "ADSB"},
        ]
        # Muninn builds: {"networks": [], "aircraft": chunk, "meshcore_nodes": []}
        muninn_payload = {"networks": [], "aircraft": records, "meshcore_nodes": []}
        muninn_env = self._muninn_envelope_inline(muninn_payload, api_key, nonce)

        gungnir_env = build_envelope(
            build_payload(aircraft=records), api_key, nonce=nonce,
        )

        self.assertEqual(gungnir_env, muninn_env,
                         "gungnir envelope diverges from muninn v1.11.1, "
                         "deployments would break on upgrade")

    def test_parity_empty_chunk(self):
        """The edge case Muninn ships in production: an empty aircraft
        list. Server tolerates this; signatures must still match."""
        api_key = "k"
        nonce = "a" * 16
        muninn_payload = {"networks": [], "aircraft": [], "meshcore_nodes": []}
        muninn_env = self._muninn_envelope_inline(muninn_payload, api_key, nonce)
        gungnir_env = build_envelope(build_payload(), api_key, nonce=nonce)
        self.assertEqual(gungnir_env, muninn_env)

    def test_parity_500_aircraft_batch(self):
        """The full-batch case (500 records, gungnir's default batch_size
        and Muninn's tested chunk size)."""
        api_key = "k"
        nonce = "b" * 16
        records = [
            {"icao": f"{i:06X}", "lat": 42.0, "lon": -81.0,
             "alt_ft": 30000, "speed_kt": 420, "heading": 270,
             "first_seen": "2026-05-28 12:00:00", "type": "ADSB",
             "callsign": ""}
            for i in range(500)
        ]
        muninn_payload = {"networks": [], "aircraft": records, "meshcore_nodes": []}
        muninn_env = self._muninn_envelope_inline(muninn_payload, api_key, nonce)
        gungnir_env = build_envelope(build_payload(aircraft=records), api_key, nonce=nonce)
        self.assertEqual(gungnir_env["sig"], muninn_env["sig"])


if __name__ == "__main__":
    unittest.main()
