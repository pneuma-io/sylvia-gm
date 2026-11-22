"""Export builder/_tools.py's tool definitions as builder_tools.json, value for value.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-app/src/generate_builder_tools.py

The definitions are model-read, so their text is copied from the Python values, never
retyped. Every module-level constant is written in definition order, with each object's keys
in Python's order. This script is reference tooling for the port and is retired with the
Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.builder import _tools  # noqa: E402

OUTPUT = Path(__file__).with_name("builder_tools.json")


def main() -> None:
    names = [name for name in vars(_tools) if name.isupper() and not name.startswith("_")]
    exported = {name: getattr(_tools, name) for name in names}
    text = json.dumps(exported, ensure_ascii=True, indent=1, allow_nan=False) + chr(10)
    assert json.loads(text) == exported, "a definition is not plain JSON"
    OUTPUT.write_text(text, encoding="utf-8", newline=chr(10))
    print(len(names), "definitions:", ", ".join(names), len(text), "bytes")


if __name__ == "__main__":
    main()
