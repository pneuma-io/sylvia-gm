"""Generate reference vectors for sylvia_base::pyjson from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-base/tests/vectors/generate_pyjson_vectors.py

The output is deterministic (fixed seed). It is checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import random
import struct
import sys
from pathlib import Path

OUTPUT = Path(__file__).with_name("pyjson_vectors.json")

# The encodings the application uses (see docs/rust-port/hash-sites.md).
ENCODINGS = {
    "default": {},
    "sorted": {"sort_keys": True},
    "canonical": {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False},
    "wire": {"ensure_ascii": True, "separators": (",", ":")},
    "compact_unicode": {"ensure_ascii": False, "separators": (",", ":")},
    "unicode": {"ensure_ascii": False},
    "indent2": {"indent": 2},
    "indent2_sorted_unicode": {"indent": 2, "sort_keys": True, "ensure_ascii": False},
}


def float_inputs() -> list[str]:
    rng = random.Random(20261121)
    values: list[float] = []
    # Uniform over bit patterns: exercises every exponent, subnormals included.
    while len(values) < 3000:
        value = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if value == value and value not in (float("inf"), float("-inf")):
            values.append(value)
    # Values a game produces: short decimals, ratios, products.
    for _ in range(1500):
        values.append(round(rng.uniform(-1000, 1000), rng.randint(0, 6)))
        values.append(rng.randint(-500, 500) / rng.choice((3, 7, 10, 12, 60, 100, 1000)))
        values.append(rng.uniform(0, 1) * 10 ** rng.randint(-12, 22))
    # Exact halves between two equally short candidates: Python rounds half to even.
    for _ in range(3000):
        whole = rng.randrange(10**13, 10**16)
        values.append(whole + rng.choice((0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875)))
        values.append((rng.randrange(1, 2**53) | 1) * 2.0 ** rng.randint(-60, 60))
    # The boundaries where Python switches between fixed and exponent notation.
    for exponent in range(-30, 31):
        for mantissa in (1.0, 1.5, 9.999999999999998, 1.2345678901234567, 5.0):
            values.append(mantissa * 10.0**exponent)
            values.append(-mantissa * 10.0**exponent)
    values += [
        0.0, -0.0, 1.0, -1.0, 0.1, 0.2, 0.1 + 0.2, 1 / 3, 2 / 3, 1e15, 1e16, 1e17, 9999999999999998.0,
        1e-4, 1e-5, 123456789012345.6, 1234567890123456.7, 5e-324, 2.2250738585072014e-308,
        1.7976931348623157e308, 4.9406564584124654e-324, 9007199254740993.0, 0.30000000000000004,
        100.0, 1e2, 1e22, 1e23, 72057594037927936.0,
    ]
    return [repr(value) for value in values]


def structured_inputs() -> list[str]:
    return [
        "null", "true", "false", "0", "-0", "1", "-1", "9223372036854775807",
        "-9223372036854775808", "18446744073709551615", "1.0", "1.50", "1E5", "1e5", "-1e-7",
        "2.5e+3", "0.1", "-0.0", "1e16", "1e15", "123456789.123456789",
        '""', '"plain"', '"quote \\" backslash \\\\ slash /"',
        '"controls \\b\\f\\n\\r\\t \\u0000 \\u0001 \\u001f \\u007f \\u0080 \\u009f"',
        '"caf\\u00e9 na\\u00efve \\u65e5\\u672c\\u8a9e \\ud83c\\udfb2 \\ud83d\\ude00 \\u2028 \\u2029"',
        '"\\u00e9" ', '"\\ufeff\\uffff\\ue000"', '"<script>&amp;</script>"',
        "[]", "{}", "[[]]", "[{}]", '{"a":[]}', '{"a":{}}', "[1,2,3]", '[1, "two", 3.0, null, true]',
        '{"b":1,"a":2,"c":3}', '{"z":{"y":{"x":[3,2,1]}},"a":null}',
        '{"B":1,"a":2,"A":3,"b":4,"_":5,"1":6,"10":7,"2":8,"":9}',
        '{"\\u00e9":1,"e":2,"\\u00c9":3,"z":4,"\\u65e5":5,"\\ud83c\\udfb2":6,"~":7}',
        '{"dup":1,"other":2,"dup":3}',
        '{"package_id":"srd-lite","version":"0.3.28","entries":[{"sha256":"ab","size":12}],'
        '"ratio":0.5,"limit":1e3,"name":"R\\u00e8gles"}',
        '{"campaign_id":"c-1","turn":7,"action":"attack","tool":"resolve_attack",'
        '"parameters":{"target":"wolf","advantage":false,"bonus":2.0,"range_ft":30}}',
        '[1e-5,1e-4,0.001,1e21,1e22,123456789012345680000.0,0.000001]',
        '{"nested":[[1,[2,[3,[4,[5]]]]],{"k":[{"k":[{"k":[]}]}]}]}',
        '"   leading and trailing   "', '"tab\\there"', '"line\\nbreak"',
        '{"key with spaces":1,"key\\nwith\\nnewlines":2,"key\\"quote":3}',
    ]


def main() -> int:
    floats = [[text, json.dumps(json.loads(text))] for text in float_inputs()]
    cases = []
    for text in structured_inputs():
        value = json.loads(text)
        outputs = {name: json.dumps(value, **options) for name, options in ENCODINGS.items()}
        canonical = outputs["canonical"].encode("utf-8")
        cases.append({
            "text": text,
            "outputs": outputs,
            "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        })
    document = {
        "python": sys.version.split()[0],
        "encodings": {name: {key: list(item) if isinstance(item, tuple) else item
                             for key, item in options.items()}
                      for name, options in ENCODINGS.items()},
        "floats": floats,
        "cases": cases,
        "hashes": {
            "empty": hashlib.sha256(b"").hexdigest(),
            "sylvia_nul_e_acute": hashlib.sha256("sylvia-gm\0é".encode()).hexdigest(),
        },
    }
    OUTPUT.write_bytes(
        (json.dumps(document, ensure_ascii=True, indent=1) + "\n").encode("ascii")
    )
    print(f"{len(floats)} floats, {len(cases)} cases -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
