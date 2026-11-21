"""Vectors for hooked `json.loads`: duplicate keys and `NaN`/`Infinity` refused by hooks.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-base/tests/vectors/generate_json_hooked_vectors.py

Sylvia's strict readers call `json.loads(text, object_pairs_hook=<refuse duplicates>,
parse_constant=<refuse NaN/Infinity>)`; which failure they report depends on which one CPython's
scanner meets first. Each text is read under the four combinations of the two hooks, and the
outcome recorded: the `JSONDecodeError` text, the refused constant, the duplicate key, another
exception's class, or the value (non-finite numbers replaced by `null`) and whether a
non-finite number was read. It writes `json_hooked_vectors.json`; a re-run writes the same file.
Reference tooling only.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "json_hooked_vectors.json"


class Duplicate(ValueError):
    pass


class Constant(ValueError):
    pass


def refuse_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Duplicate(key)
        result[key] = value
    return result


def refuse_constant(literal: str) -> object:
    raise Constant(literal)


CURATED = [
    # The orderings that matter.
    '[NaN, ]', '[NaN, x]', '{"a": NaN, "a": 1}', '{"a": 1, "a": 2', '{"a": 1, "a": 2} x', '{"a": 1, "a": 2,}',
    '{"o": {"k": 1, "k": 2}, "c": NaN}', '{"c": NaN, "o": {"k": 1, "k": 2}}', '[{"k": 1, "k": 2}, Infinity]',
    '[Infinity, {"k": 1, "k": 2}]', '{"a": {"b": 1, "b": 2}, "a": 3}', '{"a": 1, "a": {"b": 1, "b": 2}}',
    '[1e999]', '[-1e999, NaN]', '{"x": 1e999, "x": 2}', '[1e999 2]', '1e999', '-1e999', '1e308', '1.7976931348623157e309',
    '[NaN]', '[Infinity]', '[-Infinity]', '-Infinity', 'NaN', 'Infinity', '[-Inf]', '[Nan]',
    '{"a": 1, "b": 2}', '{"a": 1, "a": 1}', '{"": 1, "": 2}', '{"\\u0061": 1, "a": 2}', '{}', '[]', '[[], {}]',
    '{"a": [1, {"a": 1}], "b": {"a": 2}}', '[{"x": 1}, {"x": 2, "x": 3}]', '{"k": 123456789012345678901234567890, "k": 1}',
    '[123456789012345678901234567890, NaN]', '["\\ud800", NaN]', '{"\\ud800": 1, "\\ud800": 2}',
    '[1, 2, 3', '{"a": 1', '﻿{"a": 1, "a": 2}', '{"a": 1}\n{"a": 2}', ' {"a" : NaN } ',
    '[' * 130 + ']' * 130, '[' * 20 + 'NaN' + ']' * 20, '{"a":' * 20 + '{"b": 1, "b": 2}' + '}' * 20,
]

DOCUMENTS = [
    '{"name": "Aki", "tags": ["a", "b"], "n": {"x": 1.5, "y": null}, "z": [true, false]}',
    '[1, -2.5e3, "s", {"k": "v", "j": 1}, [], {}]',
    '{"a": {"b": {"c": 1}}, "d": [1, 2, {"e": 3}]}',
]
ALPHABET = list('{}[]:,"\\ \n-+.0123456789eE') + ['"k": 1, ', '"k": 2', "NaN", "Infinity", "-Infinity", "1e999", '"a"']


def plain(value: object) -> tuple[object, bool]:
    """The value with non-finite floats replaced by None, and whether there were any."""
    found = False

    def walk(item: object) -> object:
        nonlocal found
        if isinstance(item, float) and not math.isfinite(item):
            found = True
            return None
        if isinstance(item, dict):
            return {key: walk(child) for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        return item

    return walk(value), found


def outcome(text: str, duplicates: bool, constants: bool) -> dict[str, object]:
    hooks: dict[str, object] = {}
    if duplicates:
        hooks["object_pairs_hook"] = refuse_duplicates
    if constants:
        hooks["parse_constant"] = refuse_constant
    try:
        value = json.loads(text, **hooks)  # type: ignore[arg-type]
    except json.JSONDecodeError as error:
        return {"decode": str(error), "lineno": error.lineno, "colno": error.colno}
    except Duplicate as error:
        # JSON-encoded, so a key holding a lone surrogate survives in this file.
        return {"duplicate": json.dumps(error.args[0])}
    except Constant as error:
        return {"constant": error.args[0]}
    except Exception as error:  # noqa: BLE001 - RecursionError and the like
        return {"other": type(error).__name__}
    data, non_finite = plain(value)
    try:
        dumped = json.dumps(data, sort_keys=True, allow_nan=False)
    except ValueError:
        dumped = None
    return {"ok": dumped, "non_finite": non_finite}


def mutations(rng: random.Random, count: int) -> list[str]:
    texts = []
    for _ in range(count):
        text = rng.choice(DOCUMENTS)
        for _ in range(rng.randint(1, 3)):
            position = rng.randrange(len(text) + 1)
            text = text[:position] + rng.choice(ALPHABET) + text[position + rng.choice((0, 0, 1)):]
        texts.append(text)
    return texts


def main() -> None:
    rng = random.Random(20261128)
    texts = list(CURATED) + mutations(rng, 800)
    seen: set[str] = set()
    unique = [text for text in texts if not (text in seen or seen.add(text))]  # type: ignore[func-returns-value]
    cases = [
        {"text": text, "outcomes": {f"{int(d)}{int(c)}": outcome(text, d, c) for d in (False, True) for c in (False, True)}}
        for text in unique
    ]
    record = {"python": sys.version.split()[0], "cases": cases}
    OUT.write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
