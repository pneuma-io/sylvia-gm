"""Generate reference vectors for sylvia_base::pystr int(), float() and repr() (lane 2).

Run with the snapshot's own interpreter (CPython 3.12, Unicode 15.0), from the repository root:

    .venv/Scripts/python.exe crates/sylvia-base/tests/vectors/generate_number_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in, so the Rust tests need no Python.
Reference tooling for the port, retired with the Python source.

`int` and `float` record the value (decimal text for `int`; `repr` and the sign for `float`)
or the `ValueError` message. `repr` records Python's result for strings, and `repr_json` for
JSON values (`repr(json.loads(text))`). (`strip` and `isspace` are lane 1's, in
`pystr_vectors.json`.)
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

OUTPUT = Path(__file__).with_name("number_vectors.json")

# Characters that matter to the parsers: ASCII digits and signs, the separators, ASCII and
# Unicode whitespace (including \x1c, which str.isspace accepts and int() does not strip), other
# scripts' digits, look-alikes, letters of inf/nan, and characters repr escapes.
NUMBER_POOL = [
    "0", "1", "5", "9", "+", "-", "_", ".", "e", "E", " ", "\t", "\n", "\x0b", "\x0c", "\r",
    "\x1c", "\x1f", "\x85", "\xa0", " ", "　", "٣", "۵", "०", "１",
    "\U0001d7ce", "²", "⅕", "Ⅻ", "i", "n", "f", "a", "t", "y", "I", "N", "F", "A",
    "x", "\x00", "'", '"', "İ",
]
TEXT_POOL = [
    "a", "Z", "'", '"', "\\", "\t", "\n", "\r", "\x00", "\x1c", "\x7f", "\x80", "\x9f", "\xa0",
    "\xad", "é", "​", " ", "　", "﻿", "日", "\U0001f3b2", "\U000e0001",
    "퟿", "", "\U0010ffff", "\U000f0000", " ", "5", "͸", " ", "ÿ",
]

FIXED_INT = [
    "0", "-0", "+0", "007", "1_000", "1__000", "_1", "1_", "+", "-", "", " ", "\t\n 42 \r\x0b\x0c",
    " 1 2 ", "1.0", "1e3", "0x10", "0b1", "0o7", "١٢٣", "１２３", "\U0001d7cf\U0001d7d0", "²", "Ⅻ",
    "\x1c5", "5\x1c", "　5　", "\xa05\xa0", "\x855", "5\x00", "-9223372036854775808",
    "9223372036854775807", "9223372036854775808", "-9223372036854775809", "9" * 40, "-" + "9" * 40,
    # The edges of the i64 range, in 19 and 20 digits and other spellings.
    "1" * 19, "9" * 19, "-" + "9" * 19, "1" * 20, "-" + "1" * 20, "+9223372036854775807",
    " -9223372036854775808 ", "9_223_372_036_854_775_807", "9_223_372_036_854_775_808",
    "0009223372036854775807", "-0009223372036854775809", "９" * 19, "٩" * 20,
    "18446744073709551615", "18446744073709551616", "-18446744073709551616",
    "1" * 4300, "1" * 4301, "0" * 4301, "_".join(["1"] * 4301), "1" * 4301 + "x", " " + "1" * 4301 + " ",
    "x" * 300, "é" * 300, "'" * 10, "'\"", "+_1", "-_1", "1_0_0", "١_٢",
]
FIXED_FLOAT = [
    "0", "-0", "0.0", "-0.0", "1.5", "+1.5", ".5", "5.", ".", "-.", "1e5", "1E5", "1e+5", "1e-5",
    "1e", "1e+", "e5", ".e5", "1.e5", "1.5e-400", "-1.5e-400", "1e400", "-1e400", "1e999999999999",
    "1e-999999999999", "2.2250738585072014e-308", "4.9406564584124654e-324", "1.7976931348623157e308",
    "1.7976931348623158e308", "1.7976931348623159e308", "0.1", "0.30000000000000004", "123456789012345678901234567890",
    "9007199254740993", "inf", "-inf", "+inf", "INF", "Inf", "infinity", "-Infinity", "INFINITY", "infinit",
    "infinityy", "nan", "-nan", "+NaN", "NAN", "nanx", "in", "na", "1_000.5", "1_000.000_1", "1_.5", "1._5",
    "1_e5", "1e_5", "1e5_0", "_1.5", "1.5_", "1__0", "i_nf", " 1.5 ", "\t1.5\n", "\x1c1.5", "1.5\x1c",
    "　1.5　", "١٫٥", "١.٥", "１.５", "1.5\x00", "0x1p3", "0x10", "1,5", "--1", "+-1", "1 .5",
    "1. 5", "1e 5", "", " ", "\xa0", "1" * 5000, "0." + "0" * 400 + "1", "1" * 400 + "e-400",
    "0" * 4301 + ".5", "'1.5'", "1.5é", "é", "x" * 300, "1e0001", "1e-0", "00.00e00",
]


def int_case(text: str) -> dict:
    try:
        value = int(text)
    except ValueError as error:
        return {"text": text, "error": str(error)}
    return {"text": text, "value": str(value) if abs(value) < 10**50 else ("big" if value > 0 else "-big")}


def float_case(text: str) -> dict:
    try:
        value = float(text)
    except ValueError as error:
        return {"text": text, "error": str(error)}
    return {"text": text, "repr": repr(value), "negative": math.copysign(1.0, value) < 0}


def json_values(rng: random.Random) -> list[str]:
    """JSON texts whose `repr(json.loads(text))` is recorded: every scalar shape and nesting."""

    def value(depth: int):
        kind = rng.randrange(9 if depth < 3 else 6)
        if kind == 0:
            return None
        if kind == 1:
            return rng.random() < 0.5
        if kind == 2:
            return rng.choice([0, 1, -1, 7, 2**31, -(2**53), 2**63 - 1, -(2**63), 2**64 - 1, rng.randint(-10**6, 10**6)])
        if kind == 3:
            return rng.choice([0.0, -0.0, 0.5, 1.0, 1e16, 1e-5, 1e22, 123.456, 0.1 + 0.2, 5e-324, rng.uniform(-1e3, 1e3)])
        if kind in (4, 5):
            return "".join(rng.choice(TEXT_POOL) for _ in range(rng.randint(0, 6)))
        if kind in (6, 7):
            return [value(depth + 1) for _ in range(rng.randint(0, 4))]
        return {"".join(rng.choice(TEXT_POOL) for _ in range(rng.randint(0, 3))): value(depth + 1)
                for _ in range(rng.randint(0, 4))}

    texts = ["null", "true", "false", "0", "-0", "1.0", "1E5", "-0.0", "[]", "{}", '""', '"\'"', '"\'\\""',
             '[[], {}]', '{"a": [1, 2.5, "x"], "b": {"c": null}}']
    texts += [json.dumps(value(0), ensure_ascii=False) for _ in range(1500)]
    return texts


def main() -> int:
    rng = random.Random(4300)
    generated = ["".join(rng.choice(NUMBER_POOL) for _ in range(rng.randint(0, 10))) for _ in range(6000)]
    digitish = [
        "".join(rng.choice("0123456789_.eE+- ") for _ in range(rng.randint(1, 12))) for _ in range(4000)
    ]
    texts = ["".join(rng.choice(TEXT_POOL) for _ in range(rng.randint(0, 8))) for _ in range(3000)]
    document = {
        "python": sys.version.split()[0],
        "int": [int_case(text) for text in FIXED_INT + generated + digitish],
        "float": [float_case(text) for text in FIXED_FLOAT + generated + digitish],
        "repr": [[text, repr(text)] for text in texts + FIXED_INT[:40]],
        "repr_json": [[text, repr(json.loads(text))] for text in json_values(random.Random(6060))],
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True) + "\n").encode("ascii"))
    print(f"{len(document['int'])} int, {len(document['float'])} float, {len(document['repr'])} repr, "
          f"{len(document['repr_json'])} repr_json -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
