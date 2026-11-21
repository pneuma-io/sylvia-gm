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
    near = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u180e", "\u00ad", "\u001b", "\u007f",
            "\u0000", "\u2800", "\u3164", "\uffa0"]
    pool = spaces + near + ["a", "\u00e9", "\u540d", "\U0001f600", "-", "0"]
    strings = ["", " ", "a", " a ", "\x1c\x1d\x1e\x1fa\x1f", "\u3000a\u3000", "\u180ea\u180e"]
    for _ in range(2000):
        strings.append("".join(rng.choice(pool) for _ in range(rng.randint(0, 8))))
    # Characters with special case mappings: multi-character folds and uppercases, title-case
    # digraphs, final sigma, Kelvin and Ohm signs, Cherokee, Deseret, Adlam, Unicode 15 additions.
    cased = [chr(c) for c in (
        *range(0x41, 0x5b), *range(0x61, 0x7b), 0xb5, 0xdf, 0x130, 0x131, 0x149, 0x17f, 0x1c4, 0x1c5,
        0x1c6, 0x1f0, 0x345, 0x390, 0x3a3, 0x3c2, 0x3c3, 0x1e96, 0x1e9e, 0x1f50, 0x1f80, 0x1f88,
        0x1fb3, 0x1fbc, 0x212a, 0x2126, 0x2c2f, 0xa7c0, 0xab70, 0x13a0, 0x13f8, 0x1c80, 0x10400,
        0x10570, 0x1e900, 0x1e921, 0xfb00, 0xfb01, 0xfb06, 0xfb13, 0x587, 0x1e030, 0x1df02,
    )]
    cased_strings = ["", "Stra\u00dfe", "\u01c5", "\u0130stanbul", "\u03a3\u0391\u03a3", "\ufb03"]
    for _ in range(1500):
        cased_strings.append("".join(rng.choice(cased + ["\u00e9", "\u540d", "\U0001f600", " "]) for _ in range(rng.randint(0, 10))))
    vectors = {
        "python": sys.version.split()[0],
        "unicode": unicodedata.unidata_version,
        "whitespace": [ord(c) for c in spaces],
        "strings": [
            {"text": text, "strip": text.strip(), "lstrip": text.lstrip(), "rstrip": text.rstrip(),
             "isspace": text.isspace()}
            for text in strings
        ],
        "cased": [
            {"text": text, "casefold": text.casefold(), "upper": text.upper(), "len": len(text)}
            for text in cased_strings
        ],
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
