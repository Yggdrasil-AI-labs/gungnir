"""Envelope tests.

The byte-identical-output test is load-bearing: it proves gungnir's
build_envelope() produces the same signature Muninn v1.11.1 produces for
the same (payload, nonce, key). If this ever breaks, every existing
deployment's signatures stop matching what the server expects.
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

from gungnir.envelope import build_envelope, build_payload  # noqa: E402


class EnvelopeTests(unittest.TestCase):

    def test_build_payload_fills_empty_slots(self):
        p = build_payload(aircraft=[{"icao": "A8A5DD"}])
        self.assertEqual(p["aircraft"], [{"icao": "A8A5DD"}])
        self.assertEqual(p["networks"], [])
        self.assertEqual(p["meshcore_nodes"], [])

    def test_build_payload_none_means_empty(self):
        p = build_payload()
        self.assertEqual(p, {"networks": [], "aircraft": [], "meshcore_nodes": []})

    def test_envelope_signature_matches_muninn_v1_11_1(self):
        """Reproduce muninn.py upload() bytes for a fixed input.

        If this fails, gungnir is not a drop-in replacement for muninn's
        envelope build. Every deployed feeder's signatures stop matching
        what the server expects."""
        api_key = "test-key-12345678"
        nonce = "deadbeefcafebabe"
        records = [{"icao": "A8A5DD", "lat": 42.0, "lon": -81.0}]
        payload = build_payload(aircraft=records)

        env = build_envelope(payload, api_key, nonce=nonce)

        # Recompute the expected signature by hand using the same
        # algorithm Muninn used. This is the contract:
        #   sig = hmac_sha256(key, nonce + base64(json(payload)))
        body_json = json.dumps(payload, separators=(",", ":"))
        expected_data_b64 = base64.b64encode(body_json.encode()).decode()
        expected_sig = hmac.new(
            api_key.encode(),
            (nonce + expected_data_b64).encode(),
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(env["data"], expected_data_b64)
        self.assertEqual(env["nonce"], nonce)
        self.assertEqual(env["sig"], expected_sig)

    def test_envelope_deterministic_with_fixed_nonce(self):
        """Same payload + same nonce + same key → identical envelope.
        Important for testability."""
        a = build_envelope(build_payload(aircraft=[{"x": 1}]), "k", nonce="aaaaaaaaaaaaaaaa")
        b = build_envelope(build_payload(aircraft=[{"x": 1}]), "k", nonce="aaaaaaaaaaaaaaaa")
        self.assertEqual(a, b)

    def test_envelope_different_nonce_different_sig(self):
        a = build_envelope(build_payload(aircraft=[{"x": 1}]), "k", nonce="a" * 16)
        b = build_envelope(build_payload(aircraft=[{"x": 1}]), "k", nonce="b" * 16)
        self.assertNotEqual(a["sig"], b["sig"])

    def test_payload_key_order_stable(self):
        """The server is tolerant about key order in the inner JSON, but
        we sign the bytes. So stable ordering means stable signatures
        for the same logical payload."""
        p = build_payload(aircraft=[{"icao": "A"}], networks=[{"bssid": "B"}])
        body = json.dumps(p, separators=(",", ":"))
        # networks before aircraft before meshcore_nodes (Python 3.7+ dict
        # ordering preserved; build_payload constructs in this order)
        self.assertEqual(
            body,
            '{"networks":[{"bssid":"B"}],"aircraft":[{"icao":"A"}],"meshcore_nodes":[]}',
        )


if __name__ == "__main__":
    unittest.main()
