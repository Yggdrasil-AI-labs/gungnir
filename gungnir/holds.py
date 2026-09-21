"""Don't spend a sync on records the server already has.

A feeder on a timer re-sends the same records every cycle. The server counts
those as syncs that carried nothing new and, since the Uplink page landed,
says so out loud ("your device has sent N syncs in a row with nothing new in
them"). It is right: those records were accepted on an earlier push. The fix
is to not make the request at all when every record in the payload is one we
know the server has.

This lives in gungnir rather than in each feeder because all three hit it and
the first two fixes had already drifted apart before the third was written.
It is a primitive, not a transport hook: Muninn posts HMAC JSON through
`transport.send`, wigle-to-wdgwars posts multipart CSV through its own
uploader, and heimdall has its own envelope entirely. A hook inside `send`
would only ever reach Muninn.

Holds are stored as **expiry times**, not send times, so two different hold
lengths coexist in one map. Two lengths, because the server tells us
different things:

- `CONFIRMED_TTL` (a day) when an upload came back having imported nothing.
  That response means the server already held every record in that payload:
  its own verdict, the one case where it effectively itemises what it has.
- `SENT_TTL` (an hour) when the server did import something, or when the
  response could not be read. A mixed response does not say WHICH records
  were new, so nothing in it earns the longer hold.

Measured on a live ADS-B receiver 2026-09-21: aircraft turn over completely
inside half an hour, so the hour-long hold almost never suppresses a daytime
cycle on its own. The confirmed hold is what actually answers the warning.

Every failure path here errs toward uploading. An unreadable state file, an
unwritable config dir, a record whose identity we cannot read: all of them
cost one redundant upload and none of them suppress one.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .keys import config_dir

log = logging.getLogger(__name__)

SENT_TTL = 3600
CONFIRMED_TTL = 86400

# Which field identifies a record, per upload slot. These match the schemas
# the server validates, so a change here means the wire shape changed too.
IDENTITY_FIELDS = {
    "aircraft": ("icao",),
    "networks": ("bssid", "netid", "mac"),
    "meshcore_nodes": ("node_id",),
}


def _path(tool: str) -> Path:
    return config_dir(tool) / "holds.json"


def identity(record: dict, slot: str) -> str | None:
    """The identifying value for ``record``, or None if it cannot be read.

    None means "always upload this one". A record we cannot key is a record
    we must never suppress: dropping an observation over a bookkeeping field
    is a worse outcome than sending it twice.
    """
    for field in IDENTITY_FIELDS.get(slot, ()):
        value = record.get(field)
        if value:
            return str(value).strip().upper()
    return None


def load(tool: str) -> dict[str, float]:
    """Read the identity -> hold-expiry map. Any problem reading it degrades
    to "we hold nothing"."""
    try:
        data = json.loads(_path(tool).read_text())
        return {str(k): float(v) for k, v in data.items()}
    except Exception:
        return {}


def save(tool: str, state: dict[str, float]) -> None:
    """Write the map atomically.

    Temp file plus rename, because a feeder can legitimately have two
    processes against one config dir (a watch daemon and a periodic task, both
    of which the --schedule installers can create). A reader must never see a
    half-written file. Concurrent writers can still lose each other's entries,
    last write wins, and the cost of a lost entry is one redundant upload.
    """
    path = _path(tool)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, path)
    except OSError as e:
        log.warning("could not persist holds to %s (%s); every cycle will "
                    "upload in full", path, e)
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def prune(state: dict[str, float], now: float) -> dict[str, float]:
    """Drop expired holds. Values are expiry times, so this needs no TTL of
    its own and holds of different lengths coexist."""
    return {k: exp for k, exp in state.items() if exp > now}


def is_held(key: str | None, state: dict[str, float], now: float) -> bool:
    """Whether ``key`` is still held. A key of None is never held.

    The key-level entry point, for callers whose records are not dicts.
    wigle-to-wdgwars uploads WiGLE CSV rows and its identity is the MAC and
    SSID together, not one field, so it builds its own keys and comes in
    here. Normalisation is the caller's business at this level: a MAC is
    case-insensitive but an SSID is not, and only the caller knows which
    half is which.
    """
    if key is None:
        return False
    expires = state.get(key)
    return expires is not None and expires > now


def unheld(records: list[dict], slot: str, state: dict[str, float],
           now: float) -> list[dict]:
    """The records whose hold has expired or was never set."""
    return [r for r in records
            if not is_held(identity(r, slot), state, now)]


def record_keys(tool: str, keys, now: float, ttl: float = SENT_TTL) -> None:
    """Hold every key in ``keys`` until ``now + ttl``.

    An existing longer hold wins: a confirmed record turning up in a later
    mixed payload must not have its day-long hold cut back to an hour.
    """
    state = prune(load(tool), now)
    expires = now + ttl
    for key in keys:
        if key:
            state[key] = max(state.get(key, 0.0), expires)
    save(tool, state)


def record_sent(tool: str, records: list[dict], slot: str, now: float,
                ttl: float = SENT_TTL) -> None:
    """Hold every record in ``records`` until ``now + ttl``."""
    record_keys(tool, (identity(r, slot) for r in records), now, ttl)


def ttl_for(imported: int | None) -> float:
    """How long to hold a payload, given what the server imported from it.

    ``None`` means the count could not be read, which is not the same as zero
    and must not earn the long hold.
    """
    return CONFIRMED_TTL if imported == 0 else SENT_TTL
