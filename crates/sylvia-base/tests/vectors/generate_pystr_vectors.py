"""Generate reference vectors for sylvia_base::pystr from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-base/tests/vectors/generate_pystr_vectors.py

The output is deterministic (fixed seed). It is checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import random
import sys
import unicodedata
from pathlib import Path

OUTPUT = Path(__file__).with_name("pystr_vectors.json")


def main() -> None:
    rng = random.Random(20261121)
    spaces = [chr(c) for c in range(sys.maxunicode + 1) if chr(c).isspace()]
    # Characters that look like whitespace, or were whitespace in older Unicode, but are not.
    near = ["​", "‌", "‍", "⁠", "﻿", "᠎", "­", "\u001b", "\u007f",
            "\u0000", "⠀", "ㅤ", "ﾠ"]
    pool = spaces + near + ["a", "é", "名", "😀", "-", "0"]
    strings = ["", " ", "a", " a ", "\x1c\x1d\x1e\x1fa\x1f", "　a　", "᠎a᠎"]
    for _ in range(2000):
        strings.append("".join(rng.choice(pool) for _ in range(rng.randint(0, 8))))
    vectors = {
        "python": sys.version.split()[0],
        "unicode": unicodedata.unidata_version,
        "whitespace": [ord(c) for c in spaces],
        "strings": [
            {"text": text, "strip": text.strip(), "lstrip": text.lstrip(), "rstrip": text.rstrip(),
             "isspace": text.isspace()}
            for text in strings
        ],
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
