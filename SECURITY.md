# Security Notes

gungnir is the shared HMAC transport **library** for the WDGoWars feeder
family (Muninn / adsb-to-wdgwars, wigle-to-wdgwars, wdgwars-api-tester).
It has no CLI, parses no arguments, and runs no subprocesses.

## What this library does

- Builds HMAC-SHA256-signed JSON envelopes and POSTs them to the
  WDGoWars endpoint the consuming feeder configures
  (`https://wdgwars.pl/endpoint/upload/` by default).
- Resolves, saves, and redacts the WDGoWars API key on the consumer's
  behalf (see below).
- Persists two small state files per tool in the per-tool config dir:
  `cooldown.json` (429 backpressure) and `hwm.json` (last successful
  upload, for external monitoring).

## What it does not do

- ❌ **No telemetry or analytics.** The only outbound traffic is the
  upload/`/api/me` requests the consuming feeder asks for.
- ❌ **No version check, no self-update.** Consumers pin a release tag in
  `requirements.txt` and bump deliberately.
- ❌ **No `eval`, `exec`, subprocesses, or shell usage.**
- ❌ **No runtime dependencies.** Pure stdlib (`urllib`, `ssl`, `hmac`,
  `json`); nothing is fetched at runtime.

## API key handling

- Resolution precedence: explicit `cli_key` argument (the consumer's
  `--key` flag) → `$WDGWARS_API_KEY` → `<config_dir>/<tool>/api.key`.
- `save_key()` refuses symlinked key files and creates the file
  `0o600` atomically (`O_WRONLY|O_CREAT|O_TRUNC`).
- `scrub()` redacts the key from every log line the library emits.
- Keys travel over HTTPS only (`ssl.create_default_context()` — system
  trust store, hostname verification, TLS 1.2+), in the `X-API-Key`
  header.

See [SECURITY-FINDINGS.md](SECURITY-FINDINGS.md) for the static-analysis
review and hotspot dispositions.

## Reporting a vulnerability

Open a private security advisory on the
[Security tab](https://github.com/Yggdrasil-AI-labs/gungnir/security/advisories)
of this repo, or open a regular issue if the report is not sensitive.
Transport-level issues usually affect every consuming feeder — please
report here (the single point of fix) rather than against a feeder.
