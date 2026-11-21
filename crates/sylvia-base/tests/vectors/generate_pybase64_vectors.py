"""Generate reference vectors for sylvia_base::pybase64 from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-base/tests/vectors/generate_pybase64_vectors.py

Records `base64.decodebytes` (non-strict `binascii.a2b_base64`), `base64.b64decode(s,
validate=True)`, `base64.b64encode` and `base64.encodebytes`. Reference tooling only; retired
with the Python source.
"""

from __future__ import annotations

import base64
import binascii
import json
import random
import sys
from pathlib import Path

OUTPUT = Path(__file__).parent / "pybase64_vectors.json"

CRAFTED = [
    "", "=", "==", "===", "====", "a", "a=", "a==", "ab", "ab=", "ab==", "abc", "abc=", "abcd", "abcd=",
    "abcd==", "YQ", "YQ=", "YQ==", "YQ===", "YWI", "YWI=", "YWI==", "YWJj", "YWJjZA", "YWJjZA=", "YWJjZA==",
    "=YWJj", "Y=Q==", "YQ=x", "YQ==x", "YQ== ", " YQ==", "YQ\n==", "YW Jj", "YW\nJj\n", "!!!!YWJj", "YWJj!!",
    "aGVsbG8=", "aGVs bG8=", "a=b", "ab==cd", "Y W J j", "YWJj====", "YWJjZA=x", "YWJjZA==YWJj", "+/+/", "-_-_",
    "YWJj\r\n", "é", "YWéJj", "\x00YWJj", "YQ=\x00",
]


def decode_record(function, text: str):
    try:
        return {"ok": function(text).hex()}
    except (binascii.Error, ValueError) as error:
        return {"error": type(error).__name__, "message": str(error)}


def main() -> int:
    rng = random.Random(20261121)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/" * 2 + "==== \n!-_."
    texts = list(CRAFTED)
    for _ in range(3000):
        texts.append("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14))))
    decodes = []
    for text in texts:
        record = {"text": text, "validate": decode_record(lambda t: base64.b64decode(t, validate=True), text)}
        if text.isascii():
            record["decodebytes"] = decode_record(lambda t: base64.decodebytes(t.encode("ascii")), text)
        decodes.append(record)
    encodes = []
    for length in list(range(0, 130)) + [171, 172, 1000]:
        data = bytes(rng.randrange(256) for _ in range(length))
        encodes.append({
            "data": data.hex(),
            "b64encode": base64.b64encode(data).decode("ascii"),
            "encodebytes": base64.encodebytes(data).decode("ascii"),
        })
    document = {"python": sys.version.split()[0], "decodes": decodes, "encodes": encodes}
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(decodes)} decodes, {len(encodes)} encodes -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
