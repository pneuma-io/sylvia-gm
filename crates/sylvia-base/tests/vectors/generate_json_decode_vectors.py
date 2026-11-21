"""Vectors for `json.loads` failures: CPython's `JSONDecodeError` messages and positions.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-base/tests/vectors/generate_json_decode_vectors.py

It writes `json_decode_vectors.json`: for each text, `json.loads`'s outcome, which is the
exception class and `str(error)`, or the value as `json.dumps(sort_keys=True)` writes it. The
cases are curated texts (every message, positions after multi-line and non-BMP text, the
byte-order mark, the texts Python reads but `sylvia_base::pyjson::loads` refuses by declaration),
every prefix of several documents, and seeded random edits of valid documents. A re-run writes
the same file. Reference tooling only.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "json_decode_vectors.json"

CURATED = [
    "", " ", "\n\t ", "{", "[", "}", "]", ",", ":", "x", "{}", "[]", "{} ", " [] \n",
    "[1,]", "[1,,2]", "[,1]", "[1 2]", "[1\n2]", "[1,\n]", '{"a":1,}', "{,}", '{"a" 1}', '{"a":}',
    '{"a":1 "b":2}', '{"a"}', "{'a':1}", '{1:2}', '{"a":1}}', "[[]", "[[]]]", '{"a":[}', '[{"a":1]',
    '"abc', '"', '"\\', '"\\"', '"a\\x"', '"a\\ "', '"\\u12"', '"\\u12g4"', '"\\u"', '"\\u123"',
    '"\\u1234', '"\\ud800\\u12"', '"\\ud800\\uzzzz"', '"\\ud800\\udc00"', '"\\ud800\\u0041"',
    '"\\ud800"', '"\\udc00"', '"a\u0001b"', '"tab\there"', '"line\nbreak"', '"\x1f"', '"\x7f"',
    "nul", "null", "nulL", "tru", "true", "fals", "false", "falsey", "nullx", "-", "--1", "01", "1.",
    "1.e5", "1e", "1e+", "1E-", "1e5", ".5", "+1", "0x10", "-0", "-01", "00", "1.5.2", "1e5e5",
    "[NaN]", "NaN", "Infinity", "-Infinity", "-Inf", "Nan", "infinity", "1e400", "-1e400",
    "123456789012345678901234567890", "-123456789012345678901234567890",
    "\ufeff{}", "\ufeff", "{}x", "[] []", "1 2", '"a" "b"', "true false",
    '{\n  "name": "Aki",\n  "age": 3\n  "x": 1\n}', '[\n1,\n2,\n\n  oops]',
    '{"é": "café", "x": }', '{"😀": 1, "y" 2}', '["😀😀", nope]', '"😀\\q"', "é", "😀",
    '{"a": "\\u00e9"} trailing', '[1, 2, 3', '{"a": {"b": [1, 2, {"c": tru}]}}',
    "[" * 200 + "]" * 200, "[" * 1100 + "]" * 1100, "[" * 50, '{"a":' * 30,
    " \r\n{\r\n\"a\"\r\n:\r\n1\r\n}\r\n", "\u00a0{}", "{}\u00a0", "\u2028[]",
]

DOCUMENTS = [
    '{"name": "Aki", "tags": ["a", "b"], "nested": {"x": 1.5, "y": null, "z": [true, false]}}',
    '[1, -2.5e3, "s\\u00e9\\n", {"k": "v"}, [], {}]',
    '{"\\ud83d\\ude00": "😀", "path": "a\\/b", "escape": "\\"q\\""}',
    '  {"a" : [ 1 , 2 ] , "b" : { } }  ',
]

ALPHABET = list('{}[]:,"\\ \n\t-+.0123456789eEnutrlfasINy') + ["é", "😀", "\x00", "\x1f", "u"]


def outcome(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        return {"text": text, "error": "JSONDecodeError", "message": str(error)}
    except Exception as error:  # noqa: BLE001 - RecursionError and the like are recorded too
        return {"text": text, "error": type(error).__name__}
    try:
        dumped = json.dumps(value, sort_keys=True, allow_nan=False)
    except ValueError:
        dumped = None
    if isinstance(value, float) and not math.isfinite(value):
        dumped = None
    return {"text": text, "ok": dumped}


def mutations(rng: random.Random, count: int) -> list[str]:
    texts = []
    for _ in range(count):
        text = list(rng.choice(DOCUMENTS))
        for _ in range(rng.randint(1, 3)):
            position = rng.randrange(len(text) + 1)
            action = rng.random()
            if action < 0.4 and position < len(text):
                del text[position]
            elif action < 0.7:
                text.insert(position, rng.choice(ALPHABET))
            elif position < len(text):
                text[position] = rng.choice(ALPHABET)
        texts.append("".join(text))
    return texts


def main() -> None:
    rng = random.Random(20261127)
    texts = list(CURATED)
    for document in DOCUMENTS:
        texts.extend(document[:end] for end in range(len(document)))
    texts.extend(mutations(rng, 2500))
    seen: set[str] = set()
    unique = [text for text in texts if not (text in seen or seen.add(text))]  # type: ignore[func-returns-value]
    record = {"python": sys.version.split()[0], "cases": [outcome(text) for text in unique]}
    OUT.write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
