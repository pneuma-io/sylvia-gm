r"""Export the No Ruleset mechanics check's patterns and correction as ruleless_mechanics.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-app/src/generate_ruleless_mechanics.py

Read back from `application/ruleless_play.py`, never retyped:

- `patterns`: `_MECHANICS_PATTERNS`, each label with its compiled pattern's source (the
  `_SKILL_WORDS` concatenations already made) and whether it is `re.IGNORECASE`;
- `negation`: `_NEGATION`'s source;
- `code` and `instruction`: `RULELESS_MECHANICS_REJECTION_CODE` and
  `RULELESS_MECHANICS_INSTRUCTION`, whose `{findings}` the Rust side fills as `str.format` does;
- `tables`: what Python's `re` matches, measured over every code point, so the Rust side spells
  each construct as an explicit class instead of trusting its own engine's tables: `\w`, `\s`
  and `\d` (as `[start, end]` ranges), and under `re.IGNORECASE` each ASCII letter and the
  class `[a-z]` (as the code points they match).

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import ruleless_play  # noqa: E402

OUTPUT = Path(__file__).with_name("ruleless_mechanics.json")


def ranges(matches) -> list[list[int]]:
    found: list[list[int]] = []
    for point in range(0x110000):
        if 0xD800 <= point <= 0xDFFF or not matches(chr(point)):
            continue
        if found and found[-1][1] == point - 1:
            found[-1][1] = point
        else:
            found.append([point, point])
    return found


def tables() -> dict[str, object]:
    folded = {}
    for letter in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
        pattern = re.compile(letter, re.IGNORECASE)
        folded[letter] = [point for start, end in ranges(pattern.fullmatch)
                          for point in range(start, end + 1)]
    lower = re.compile("[a-z]", re.IGNORECASE)
    return {
        "word": ranges(re.compile(r"\w").fullmatch),
        "space": ranges(re.compile(r"\s").fullmatch),
        "digit": ranges(re.compile(r"\d").fullmatch),
        "letters": folded,
        "lower_class": [point for start, end in ranges(lower.fullmatch)
                        for point in range(start, end + 1)],
    }


def main() -> None:
    patterns = []
    for label, pattern in ruleless_play._MECHANICS_PATTERNS:
        assert pattern.flags & ~(re.IGNORECASE | re.UNICODE) == 0, label
        patterns.append({"label": label, "pattern": pattern.pattern,
                         "ignorecase": bool(pattern.flags & re.IGNORECASE)})
    negation = ruleless_play._NEGATION
    assert negation.flags & ~(re.IGNORECASE | re.UNICODE) == 0
    data = {
        "patterns": patterns,
        "negation": {"pattern": negation.pattern,
                     "ignorecase": bool(negation.flags & re.IGNORECASE)},
        "code": ruleless_play.RULELESS_MECHANICS_REJECTION_CODE,
        "instruction": ruleless_play.RULELESS_MECHANICS_INSTRUCTION,
        "tables": tables(),
    }
    assert data["instruction"].count("{findings}") == 1
    assert "{" not in data["instruction"].replace("{findings}", "")
    OUTPUT.write_bytes((json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {OUTPUT.name}: {len(patterns)} patterns")


if __name__ == "__main__":
    main()
