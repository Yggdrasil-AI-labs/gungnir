# Security review — findings & disposition

On **2026-06-21**, as part of bringing the WDGoWars feeder family onto a common
gated CI pipeline (pytest + coverage → SonarCloud), the `gungnir` library was
reviewed for the same classes of issue that SonarCloud's SAST flagged in the
consumer repo **adsb-to-wdgwars (Muninn)** — path traversal, command/argument
injection, insecure temp-directory use, and unsafe database opens.

**Outcome: no remediation needed.** gungnir is a small, dependency-free,
pure-stdlib library with no command-line surface, so none of those finding
classes have a vector here.

## Why the Muninn finding classes don't apply

| Muninn finding class | Status in gungnir |
|---|---|
| **S2083** — path traversal into a state file | **N/A** — gungnir takes no `argv`. The only path it builds is the per-tool key file under the OS config dir (`config_dir(tool)/api.key`), derived from a caller-supplied tool *name*, not a filesystem path. |
| **S5443** — publicly-writable / `/tmp` directory | **N/A** — no `tempfile`, `gettempdir`, or `/tmp` use; config lives under `%APPDATA%` / XDG. |
| **S8706** — SQLite connection from a filename | **N/A** — no SQLite. |
| **S6350 / S8705** — command / OS-command injection | **N/A** — no `subprocess`, `os.system`, `eval`, `exec`, or `shell=True` anywhere in the package. |
| **S8707 / S6549** — path construction from CLI args | **N/A** — gungnir is a library; it has no CLI and parses no arguments. |

## Security-relevant code, and where it's already tested

gungnir is where the feeders' shared security primitives live, so the review
focused there. All of it is already covered by `tests/test_keys.py` and
`tests/test_envelope.py`:

- **Key-file persistence (`keys.save_key`).** Refuses to write through a
  symlink (`KeyFileSymlinkError`), and opens with `O_WRONLY|O_CREAT|O_TRUNC`
  at mode `0o600` *before* writing, so the secret is never world-readable —
  not even briefly. Covered by `test_save_key_refuses_to_follow_symlink` and
  `test_save_key_writes_with_restrictive_mode_posix`.
- **Key redaction (`keys.scrub`).** Redacts the API key from any string before
  it can be logged, so a server response that echoes the key never spills into
  logs. Covered by the `test_scrub_*` tests.
- **HMAC envelope (`envelope`).** Signs each payload with a per-request nonce;
  the exact JSON serialization is load-bearing for signature stability.
  Covered by `tests/test_envelope.py`.
- **TLS.** Outbound requests use the stdlib default `ssl` context (system trust
  store, hostname verification, TLS 1.2+).

No new tests were added here because the existing suite already locks these
behaviors; duplicating them under a `test_security.py` would add noise, not
coverage. The CI quality gate now runs that suite with coverage on every
change.

## SonarCloud hotspot disposition

SonarCloud has been wired since 2026-07-02 (hard gate,
`sonar.qualitygate.wait=true` — see [CI.md](CI.md)). If the scanner raises
**security hotspots** (review-required, not vulnerabilities) on the `os.open`
key-file write or the `hmac`/`ssl` usage, the disposition above is the
rationale to mark those *Safe*: the key file is created `0o600` behind a
symlink check, and the crypto uses stdlib primitives as intended.
