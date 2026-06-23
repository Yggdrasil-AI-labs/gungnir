# gungnir

<p align="center">
  <a href="https://github.com/Yggdrasil-AI-labs/gungnir/actions/workflows/ci-quality-gates.yml"><img alt="CI" src="https://github.com/Yggdrasil-AI-labs/gungnir/actions/workflows/ci-quality-gates.yml/badge.svg"></a>
  <a href="https://sonarcloud.io/dashboard?id=Yggdrasil-AI-labs_gungnir"><img alt="Quality gate" src="https://sonarcloud.io/api/project_badges/measure?project=Yggdrasil-AI-labs_gungnir&metric=alert_status"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-b08850.svg"></a>
</p>

> *Odin's spear. Always hits its target.*

Shared transport client for the [WDGoWars](https://wdgwars.pl) (wdgwars.pl)
ecosystem of feeders. Speaks the HMAC-signed `/api/upload/` envelope, handles
cooldown persistence, retries 429s, and detects the silent-drop failure mode
where the server returns `HTTP 200 ok:true` with zero on every counter.

## Family

Sibling repos in the WDGoWars feeder family:

- [Muninn](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) — ADS-B feeder
- [Heimdall](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars) — MeshCore LoRa feeder
- [wigle-to-wdgwars](https://github.com/Yggdrasil-AI-labs/wigle-to-wdgwars) — WiGLE Wi-Fi/BLE feeder
- [wdgwars-api-tester](https://github.com/Yggdrasil-AI-labs/wdgwars-api-tester) — API surface probe

## Quick start

```python
import gungnir

client = gungnir.Client(tool="my-feeder", version="1.0.0")
key = client.load_key(cli_key=None)  # falls through CLI → env → file

records = [{"icao": "A8A5DD", "lat": 42.0, "lon": -81.0, ...}]
client.send(key, aircraft=records)
```

## API surface

```python
class Client:
    def __init__(self, tool: str, version: str, *,
                 api_url: str = DEFAULT_API_URL,
                 me_url: str = ME_API_URL): ...

    def load_key(self, cli_key: str | None = None) -> str: ...
    def save_key(self, key: str) -> None: ...
    def whoami(self, key: str) -> int: ...
    def send(self, key: str, *,
             aircraft: list[dict] | None = None,
             networks: list[dict] | None = None,
             meshcore_nodes: list[dict] | None = None,
             batch_size: int = 500,
             dry_run: bool = False) -> int: ...
```

## Why "gungnir"?

Norse mythology — Odin's spear, said to always hit its mark when thrown.
Fits a delivery client whose job is reliable, signed delivery to wdgwars.pl.
Continues the lab-wide Norse naming convention (alongside Muninn and Heimdall).

## License

MIT
