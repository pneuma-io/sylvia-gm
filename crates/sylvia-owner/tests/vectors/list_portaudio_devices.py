"""List this machine's PortAudio inputs as the Python worker lists them, for `dictation_devices`.

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/list_portaudio_devices.py <file>

Writes the Python worker's own `input_devices(sounddevice)` (ids and labels) with each device's
name and host, to a file outside the checkout: device names describe the machine, so the list
is never committed. Metadata only; no stream is opened. Reference tooling for the port, retired
with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: list_portaudio_devices.py <output file outside the checkout>")
    output = Path(sys.argv[1]).resolve()
    if output.is_relative_to(ROOT):
        raise SystemExit("write the list outside the checkout")
    sys.path.insert(0, str(ROOT / "src"))
    import sounddevice

    from sylvia_gm.local_dictation import input_devices

    rows, hosts = sounddevice.query_devices(), sounddevice.query_hostapis()
    devices = []
    for device in input_devices(sounddevice):
        row = rows[device["index"]] if device["id"] != "default" and device["index"] >= 0 else None
        devices.append({
            "id": device["id"], "label": device["label"],
            "name": row["name"] if row else None,
            "host": hosts[row["hostapi"]]["name"] if row else None,
        })
    output.write_text(json.dumps(devices, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
