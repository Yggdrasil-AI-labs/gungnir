# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-09-21 - A key-level entry point for holds

### Added

- **`holds.is_held(key, state, now)` and `holds.record_keys(tool, keys, ...)`**,
  for callers whose records are not dicts. wigle-to-wdgwars uploads WiGLE
  CSV rows, and a network's identity on WDGWars is the MAC and SSID
  together rather than one field, so it builds its own keys.

  Normalisation is deliberately the caller's job at this level. A MAC is
  case-insensitive and an SSID is not, and only the caller knows which half
  of a composite key is which: folding the whole thing would silently
  suppress one of two networks whose names differ only in case.

  `record_sent` is now a thin wrapper over `record_keys`, so the dict path
  and the key path cannot drift apart.

## [0.2.1] - 2026-09-21 - One source of truth for the version

### Fixed

- **`pyproject.toml` carried its own version literal and had already drifted
  from the package.** 0.2.0 shipped with code saying 0.2.0 and distribution
  metadata saying 0.1.6, so `pip install --upgrade` was a no-op: pip already
  had "0.1.6" and the new tarball also called itself 0.1.6. The installed
  code changed underneath a version number that did not, which is exactly
  what Muninn's gungnir version guard exists to catch, arriving from the
  packaging side instead.

  The version is now declared dynamic and read from
  `gungnir.__version__.__version__`. A test fails if a literal is ever put
  back, and another compares the installed distribution metadata against the
  code.

  **Pin v0.2.1, not v0.2.0.** The v0.2.0 tag's sdist understates its own
  version and anything resolving it by metadata will misbehave.

## [0.2.0] - 2026-09-21 - Shared already-sent holds

### Added

- **`gungnir.holds`**: the "don't spend a sync on records the server already
  has" primitive, shared by every feeder instead of written three times.

  Muninn grew this over v2.3.0-2.4.0 and it took four attempts to get right.
  wigle-to-wdgwars and heimdall have the same problem and neither has the
  fix, which is the case for it living here.

  It is a primitive, not a hook inside `transport.send`. Only Muninn posts
  through `send`: wigle-to-wdgwars has its own multipart CSV uploader and
  heimdall its own envelope entirely, so a transport hook would reach one of
  the three.

  Holds are stored as expiry times rather than send times, so two lengths
  coexist in one map:

  - **A day** (`CONFIRMED_TTL`) when an upload came back having imported
    nothing. That response is the server saying it already held every record
    in the payload: its own verdict, and the only case where it effectively
    itemises what it has.
  - **An hour** (`SENT_TTL`) when the server imported something, or when the
    response could not be read. A mixed response does not say WHICH records
    were new, and `ttl_for(None)` is deliberately not `ttl_for(0)`.

  Each rule here is a bug that shipped in Muninn first. Requiring the
  server's counters to account for a payload made the gate inert on a real
  feeder (three of six consecutive cycles came back one short, and the
  missing record is counted nowhere). Holding everything for an hour did
  almost nothing on a fixed station, where records turn over faster than the
  hold expires while the payload is still full of things the server has had
  for days.

  Every failure path errs toward uploading: an unreadable state file, an
  unwritable config dir, or a record whose identity cannot be read costs one
  redundant upload and never suppresses one. State is per tool and written
  atomically, since the `--schedule` installers can leave two processes
  against one config dir.

## [0.1.6] - Key validation leaves the /api/* pattern

### Changed

- **`ME_API_URL` now points at `/endpoint/me`.** Uploads moved to
  `/endpoint/*` in v0.1.2 to get out from under Cloudflare's L7 shield, but
  key validation was left on `/api/me` because a single call cannot trip a
  burst limit. That was the wrong test. The shield gates the whole `/api/*`
  pattern during an event, and that is exactly when a feeder still needs to
  validate its key; the 429 or challenge it gets back reads to the operator
  as a bad key rather than as a platform event. Every call gungnir makes is
  now on one path family.
- Verified before the switch: `/api/me` and `/endpoint/me` returned
  byte-identical 33-field 5008-byte responses for the same key
  (2026-09-15). Callers that need the old path can still pass
  `me_url=`, and hugin's `cf-l7-bypass-health` probe deliberately calls both.

## [0.1.5] - An HTML error page is not a log line

### Fixed

- **An HTML error page no longer gets logged as markup.** A player hit a
  portal maintenance window mid-upload and got a DOCTYPE, a `<meta>` block
  and a stylesheet in his terminal, with the one fact that mattered (the
  portal was not answering) buried in it. `describe_body()` now collapses an
  HTML body to one line naming its `<title>`, while JSON and plain text pass
  through as before. Applied to the 5xx retry log, the give-up log, and the
  silent-drop excerpt.
- **A 5xx is no longer reported as a rejection.** "rejected by wdgwars.pl"
  sent people looking at their own data for a fault that was never theirs:
  the server did not get far enough to judge the payload. A 5xx give-up now
  says the portal is not accepting uploads right now. A genuine 4xx
  rejection still says rejected.
- **`__version__` said 0.1.3 while the v0.1.4 tag was out.** The string was
  never bumped with the tag, so every feeder installing v0.1.4 got a library
  that self-reported 0.1.3 in its User-Agent and diagnostics. Now 0.1.5,
  matching this release.

## [0.1.4] - a dedupe reply is not a silent drop

The server can answer a re-sent payload with HTTP 200, `ok:true`, every
counter zero, and `info: "This payload was already uploaded recently - no new
processing."` That is the same shape as the v4 silent-drop bug, so
`check_silent_drop` flagged it and `send_chunk` returned rc=1.

It is not the same thing. A dedupe reply is the server declining work it has
already done: those records landed on an earlier push, nothing was lost, and
there is nothing for an operator to fix. The distinguishing evidence is that
the server says so in the clear, where the v4 bug said nothing at all.

Observed live on 2026-08-10: Muninn re-sends an unchanged ADS-B snapshot on
its hourly cadence, so 43 of 109 sleipnir-health runs over five days failed the
push job and raised an alert. Zero counters PLUS no explanation is still
treated as a silent drop, unchanged.

### Added

- `diagnostics.check_deliberate_skip(response)` returns the server's own words
  when it says it skipped a payload it already had, else None. Matches
  `already uploaded recently` / `no new processing` case-insensitively against
  the `info`, `message`, and `note` fields, never against counters. New markers
  go in `DELIBERATE_SKIP_MARKERS` without touching call sites.

### Changed

- `send_chunk` checks for a deliberate skip before the silent-drop test, logs
  the server's explanation at INFO, and returns rc=0. Callers that treated
  rc=1 as "surface this to a human" stop firing on healthy dedupes.
- `check_silent_drop` returns None when the response carries a deliberate-skip
  message, so a caller using the detector directly cannot reach the old
  verdict either.

### Compatibility

Additive. No wire-protocol change. A caller that has never seen a dedupe reply
behaves identically. Callers pinned to 0.1.3 keep the old behavior and will
keep reporting dedupes as failures.

### Also in this release (tooling and docs, carried from Unreleased)

- CI quality-gate pipeline (`.github/workflows/ci-quality-gates.yml`): pytest
  across a 3.10/3.11/3.12 matrix with coverage, a SonarCloud quality gate, and
  an sdist/wheel build, mirroring the consumer repos (Muninn, Heimdall,
  wigle-to-wdgwars). No Snyk stage. Gungnir has no runtime dependencies, so
  there is nothing to software-composition-scan.
- `sonar-project.properties`, `requirements-dev.txt`, `CI.md`, and pytest +
  coverage config (with a 75% regression floor; baseline ~78%) in
  `pyproject.toml`.
- `SECURITY-FINDINGS.md`: a review against the SonarCloud SAST finding classes
  found nothing to remediate (gungnir is a pure-stdlib library, no CLI argv,
  subprocess, scheduler, SQLite, or temp-dir use). The key-file safety the
  feeders rely on (symlink refusal + mode 600) lives here and is already
  covered by `tests/test_keys.py`.
- `SECURITY.md` (2026-07-18): vulnerability-reporting policy and an outbound
  footprint / key-handling summary, matching the family convention, gungnir
  was the only family repo without one.
- `tests/test_cooldown.py` + `tests/test_hwm.py` (2026-07-18): direct file-I/O
  tests for the two modules the transport suite only ever exercised through
  mocks, the 900 s sleep cap, record/clear round trips, corrupt-state
  no-ops, the counters extraction contract, and read()'s None-on-missing.
  Coverage baseline moves ~78% → ~87%.

### Fixed

- Org-migration metadata (2026-07-18): `pyproject.toml` `[project.urls]` and
  the CHANGELOG link definitions now point at `Yggdrasil-AI-labs` (they still
  pointed at the pre-migration `HiroAlleyCat` owner); link definitions for
  0.1.1/0.1.2/0.1.3 added and the `[Unreleased]` compare range unstuck from
  `v0.1.0...HEAD`. `SECURITY-FINDINGS.md` no longer claims the repo "is not
  yet imported into SonarCloud" (wired since 2026-07-02).

That batch was tooling/CI/docs only. It ships here alongside the API
change above.

## [0.1.3] - structured HTTP 413 handling for the wdgwars.pl 15 MB upload cap

LOCOSP rolled out a temporary 15 MB body cap on every wdgwars.pl upload
endpoint on 2026-06-05. Workaround for CloudLinux LVE killing the PHP
worker mid-buffer on bodies above roughly 20 MB. The server now returns
a structured 413 envelope with `max_bytes` + `received` instead of a
generic 500. Cap is expected to be removed in roughly two weeks when
LOCOSP completes a host migration.

Before this release, `send_chunk` would log 413 as a generic 4xx
rejection. Functionally safe (no retry loop, no API blast), but
structurally noisy: callers had no signal to differentiate "shrink your
batch" from "your key is wrong". This release adds that signal.

### Added

- 413 + `payload-too-large` envelope detection in `send_chunk`. When
  the server returns the cap envelope, gungnir logs `max_bytes`,
  `received`, and the body size, records a cooldown via
  `gungnir.cooldown.record` (default 30s, or `retry_after` if the
  server populates it), and returns `(1, envelope)` so the caller can
  see the structured fields. No retry, no BatchAborted: the caller may
  have other queued payloads to attempt with smaller bodies.
- 3 new tests in `tests/test_transport.py` covering: envelope detection
  + cooldown recording + single-attempt contract; server-supplied
  `retry_after` honored over the 30s default; non-LOCOSP 413 (CF or
  upstream HTML body) falling through to the generic rejected branch.

### Notes for callers

The HMAC envelope is an atomic signed blob, so gungnir cannot bisect
the payload on its own without invalidating the signature. Callers
that produce variable-size payloads (Muninn aircraft batches,
Heimdall mesh-node bursts) should treat a 413 as "shrink the batch
and call again". For most feeders this never triggers: aircraft and
mesh snapshots are kilobytes per cycle.

## [0.1.2] - default upload URL bypasses Cloudflare L7 rate-limit

WDGoWars portal sits behind Cloudflare. Free tier cannot skip the
`ddos_l7` phase via API. CF's automatic L7 DDoS protection per-IP-rate-
limits bursts of `/api/*` requests with HTTP 429 + error code 1027,
BEFORE the request reaches the origin's PHP. Surfaced via Muninn
batch uploads (cron-driven RTL-SDR rig accumulating a shift then
bulk-uploading) and portal-side `/profile` + `/map` fetches.

Portal-side fix shipped 2026-05-31: `/endpoint/*` is a one-line PHP
router alias of `/api/*`. Same router, same HMAC envelope, same
response, but the URL pattern doesn't match Cloudflare's automatic
pattern matching so the request reaches the origin.

### Changed

- `DEFAULT_API_URL` flipped from `https://wdgwars.pl/api/upload/` to
  `https://wdgwars.pl/endpoint/upload/`. Every feeder using gungnir's
  default (Muninn since v2.0.5, wigle-to-wdgwars on next bump,
  future Heimdall, etc.) inherits the bypass.
- `ME_API_URL` (`/api/me`) unchanged, single-call, not affected by
  burst rate-limiting.

### Compatibility

- Callers passing an explicit `api_url=` to `Client(...)` or
  `send(...)` are unaffected; the new default only matters when no
  override is given.
- The legacy `https://wdgwars.pl/api/upload/` keeps working on the
  origin. `/endpoint/*` is purely an alias, not a replacement. Tools
  can opt back into `/api/*` per-call if they need to.
- `Client.send()` and the lower-level `transport.send()` retry/
  cooldown/silent-drop behaviour is unchanged.

## [0.1.1] - save_key hardening

Lifts two defenses from Muninn v1.11.1's `save_key` into gungnir so
every tool using the library inherits them automatically.

### Added

- `KeyFileSymlinkError`: raised by `save_key()` when the target key
  file already exists as a symlink. Closes a redirect-to-arbitrary-
  file attack vector if anyone can plant a symlink in the config dir.
- `save_key()` now opens the file with `O_CREAT|O_TRUNC` at mode
  `0o600` atomically, so the file is never world-readable, not even
  for the microseconds between `write_text()` and a subsequent
  `chmod()`. Previously the perms tightened only after the write.

### Compatibility

- API surface unchanged for callers that aren't writing key files.
- `save_key()` callers that didn't catch `KeyFileSymlinkError` will
  now see the exception bubble up where previously the function would
  silently follow the symlink. Treated as a defect fix; bumped patch
  rather than minor because legitimate callers never hit the symlink
  path.

## [0.1.0] - Initial release

First release. Extracted from
[Muninn v1.11.1](https://github.com/HiroAlleyCat/adsb-to-wdgwars/releases/tag/v1.11.1)
to be shared by Muninn, Heimdall, and wigle-to-wdgwars without each tool
maintaining its own copy.

### Added

- `gungnir.Client`: high-level API with per-tool `tool`/`version`
  identity, `timeout` / `whoami_timeout` / `max_attempts` /
  `chunk_cooldown` / `user_agent_extra` defaults, and `__repr__`.
- `gungnir.envelope.build_envelope()` and `build_payload()`. HMAC-SHA256
  signed envelope for `/api/upload/`. Byte-identical to Muninn v1.11.1
  output for the same (payload, key, nonce) input, verified by a
  parity test that imports muninn.py and compares signatures.
- `gungnir.transport.send()`: batched upload to the signed endpoint.
- `gungnir.transport.whoami()`: `/api/me` identity check.
- `gungnir.keys`: API-key resolution with the documented precedence
  `cli → env → file`, plus `scrub()` for redacting keys from log lines.
- `gungnir.cooldown`: persistent server-cooldown state (`cooldown.json`
  in the per-tool config dir). Survives across cron invocations so a
  429 doesn't get hammered.
- `gungnir.hwm`: high-water-mark tracking (`hwm.json` in the per-tool
  config dir) for external monitoring.
- `gungnir.diagnostics.check_silent_drop()`: detects the
  HTTP-200-ok-true-zero-counters pattern from Muninn v1.11.1 (locosp's
  v4 server-side type validation could silently drop every record while
  returning success).

### Behavior decisions

These are the opinionated calls in v0.1.0. Each has a critic vector
attached, they're listed here so the rationale survives outside this
repo's commit history.

- **`send()` requires exactly one of `aircraft`/`networks`/`meshcore_nodes`.**
  The wire envelope allows mixing the three slots, but no real feeder
  needs that today. Forbidding mixed payloads keeps the contract clean
  and reduces "did you mean to send nothing?" surprises. Empty list is
  a no-op (returns 0); zero or multiple slots raises `ValueError`.

- **A silent drop returns `rc=1`, not just a warning.** Muninn v1.11.1
  warned but exited 0. A transitional compromise. Gungnir is strict:
  if the detector fires, the caller exits non-zero so cron sees the
  failure. Detecting a failure and reporting success is broken behavior
  for a library.

- **429 raises `BatchAborted` and stops the whole batch.** Sending more
  chunks at a rate-limited server only deepens the cooldown. The
  cooldown deadline is persisted before raising, so the next cron tick
  respects it. Callers catching `BatchAborted` may inspect
  `.retry_after`.

- **Retry transient errors with exponential backoff** (5xx and
  `URLError`). 3 attempts by default, starting at 2s. 4xx is not
  retried. Configurable via `Client(max_attempts=...)`.

- **`scrub()` redacts on any non-empty match.** Muninn required key
  length > 8 to redact; the threshold protected against nothing real
  and could leak short test keys. Short keys redact to `…`; longer
  keys redact to `<first-4>…<last-4>`.

- **`logging` module, never `print()`.** The library never configures
  handlers. Consumers do `logging.basicConfig()` (or whatever) to wire
  up routing.

- **Zero external dependencies.** Uses `urllib` from the standard
  library. `requests` would be faster (connection reuse), but the lean
  install matters more for a library meant to be embedded in small
  feeders.

- **Inter-chunk cooldown defaults to 1s.** A batched `send()` sleeps
  briefly between chunks (none after the last) so a 30-chunk batch
  doesn't blast the server back-to-back. Configurable via
  `Client(chunk_cooldown=...)`; set to 0 to disable.

- **`whoami()` does not silently clamp the caller's timeout.** The
  Client has separate `timeout` (for `send`) and `whoami_timeout`
  (default 30s) settings. If you set them explicitly, gungnir honors
  what you set.

- **User-Agent supports a `+url` suffix** per common bot-UA convention,
  configurable via `Client(user_agent_extra="https://...")`. Lets
  server admins trace traffic back to the source repo.

- **Tool name is path-validated.** `Client(tool="...")` rejects names
  containing `/`, `\`, `..`, or null bytes. Defensive. Tools self-select
  their name, but the check is free and the error message is clearer
  than what the OS would raise later.

- **PEP 561 compliance.** Ships `py.typed` marker so PyPI consumers
  using mypy/pyright pick up inline type hints automatically.

- **HWM file stores the full counters dict, not a single scalar.**
  An earlier draft extracted `last_upload_imported` from the response,
  but "imported" means different things to different slots (aircraft
  vs networks vs meshcore). The HWM JSON now exposes `counters` with
  every known counter the server returned.

- **`SilentDrop.raw_text_excerpt` is named accurately.** An earlier
  draft called it `raw_text` while in fact storing only the first 800
  chars. The suffix is load-bearing.

### Compatibility

- **Byte-identical envelope output to Muninn v1.11.1** for any
  `(payload, key, nonce)`. Existing deployments can be migrated to a
  gungnir-backed Muninn v2.0 without any wire-protocol change. Verified
  by `tests/test_muninn_parity.py`.

- **Config-dir paths preserved** when `tool="muninn"`. Muninn 1.x's
  `~/.config/muninn/api.key` (POSIX) and `%APPDATA%/muninn/api.key`
  (Windows) are read/written unchanged.

[Unreleased]: https://github.com/Yggdrasil-AI-labs/gungnir/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/Yggdrasil-AI-labs/gungnir/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Yggdrasil-AI-labs/gungnir/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Yggdrasil-AI-labs/gungnir/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Yggdrasil-AI-labs/gungnir/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Yggdrasil-AI-labs/gungnir/releases/tag/v0.1.0
