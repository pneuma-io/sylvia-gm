"""Export the tool loader's long model-read texts as tool_loader_texts.json, value for value.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/generate_tool_loader_texts.py

Read back from the Python values that produce them, never retyped:

- `mechanic_tool` and `reader_tool`: `_tool_loader.MECHANIC_TOOL` and `READER_TOOL`, whole;
- `definitions_preamble`: `definitions_record`'s text before the definitions (its record for no
  tools, which ends with the blank line the definitions follow);
- `choices_preamble`: `_fixed_tool_projection.argument_choices_record`'s text before the
  choices' JSON (its record for no tools, without the `{}`).

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _fixed_tool_projection as projection  # noqa: E402
from sylvia_gm.agent import _tool_loader as loader  # noqa: E402


def main() -> None:
    preamble, defined = loader.definitions_record([], loader.MechanicNames([], []))
    assert defined == [] and preamble.endswith("\n\n")
    choices = projection.argument_choices_record([], None)
    assert choices.endswith("{}")
    texts = {
        "mechanic_tool": loader.MECHANIC_TOOL,
        "reader_tool": loader.READER_TOOL,
        "definitions_preamble": preamble,
        "choices_preamble": choices[:-2],
    }
    out = Path(__file__).with_name("tool_loader_texts.json")
    out.write_bytes((json.dumps(texts, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
