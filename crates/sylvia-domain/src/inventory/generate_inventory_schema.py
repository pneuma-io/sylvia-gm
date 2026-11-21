"""Export `domain/inventory.py`'s `inventory_update_schema()` from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/src/inventory/generate_inventory_schema.py

The schema is a tool input schema the model reads (its descriptions included), so the port
loads exactly this value. This script is reference tooling for the port and is retired with
the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def main() -> None:
    sys.path.insert(0, str(HERE.parents[3] / "src"))
    from sylvia_gm.domain.inventory import inventory_update_schema

    text = json.dumps(inventory_update_schema(), indent=1, ensure_ascii=True) + "\n"
    (HERE / "inventory_update_schema.json").write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
