"""Write `srd_lite_legacy_character_contract.json` beside this file from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/src/generate_srd_lite_legacy_contract.py

`application/_srd_lite_character_legacy.py` holds the retained Lite 0.2 character contract as a
zlib-compressed, base64-encoded JSON literal. This exports the decoded value as JSON (keys in
Python's order) so the Rust port reads data instead of a duplicated literal; the port still
checks its fingerprint before every use, as Python does. Reference tooling for the port, retired
with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application._srd_lite_character_legacy import LEGACY_CHARACTER_CONTRACT  # noqa: E402

OUTPUT = Path(__file__).with_name("srd_lite_legacy_character_contract.json")


def main() -> None:
    OUTPUT.write_text(json.dumps(LEGACY_CHARACTER_CONTRACT, ensure_ascii=True, separators=(",", ":")) + "\n",
                      encoding="utf-8", newline="\n")
    print(OUTPUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
