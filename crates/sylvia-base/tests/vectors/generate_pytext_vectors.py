"""Generate reference vectors for sylvia_base's `pyjson::loads_bytes`, `pyre` templates,
`pystr::splitlines` and `pystr::isidentifier`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-base/tests/vectors/generate_pytext_vectors.py

Sections:
- `loads_bytes`: `json.loads(data)` on JSON texts encoded as each `detect_encoding` result names,
  with BOMs, truncation, bad bytes and encoded lone surrogates. A Python result holding a lone
  surrogate is marked `row 3`; the port refuses it.
- `templates`: `re.sub("x", template, "x")`, the whole match being "x", on random and targeted
  templates (escapes, octal, group references, names, unterminated input, newlines).
- `splitlines` and `isidentifier` on random texts.
Byte strings are hex. Outcomes are ["ok", value] or [class, message].
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

OUTPUT = Path(__file__).with_name("pytext_vectors.json")
rng = random.Random(20261122)
BACKSLASH = chr(92)


def outcome(call):
    try:
        return ["ok", call()]
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error)]


def has_surrogate(value) -> bool:
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def loads_cases() -> list:
    documents = ['{"a": [1, 2.5, "x"], "b": null}', '"text"', "[]", "1", " {} ", '{"k": "café ☃ 𝄞"}', "", "{",
                 '"\\ud800"', "nan"]
    encodings = ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"]
    datas = []
    for document in documents:
        for encoding in encodings:
            encoded = document.encode(encoding)
            datas += [encoded, encoded[:-1], encoded + b"\xff", encoded[:len(encoded) // 2]]
    datas += [b"\xef\xbb\xbf\xef\xbb\xbf{}", b'"\xed\xa0\x80"', b'"\xed\xa0\x80\xff"', b'"\xed\xb0\x80x"',
              b'"\xc3"', b"\xff\xfe\x00\xd8\x22\x00", b"\xff\xfe\x22\x00\x00\xd8", b"\x00", b"\x00\x00",
              b"1\x00", b"\x00\x00\x00\x31", b"\x31\x00\x00\x00", b"\xff\xfe\x00\x00\x00\x00\x11\x00",
              b"\x00\x00\xfe\xff\x00\x00\x00\x31\x00", b'"a\x80"', b"\xfe\xff\x00\x31\xd8"]
    for _ in range(300):
        datas.append(bytes(rng.choice([0, 0x22, 0x31, 0x5b, 0x5d, 0x7b, 0x7d, 0xd8, 0xdc, 0xed, 0xa0, 0x80, 0xff,
                                        0xfe, 0xef, 0xbb, 0xbf, 0xc3, 0x20]) for _ in range(rng.randrange(0, 9))))
    cases, seen = [], set()
    for data in datas:
        if data in seen:
            continue
        seen.add(data)
        result = outcome(lambda: json.loads(data))
        case = {"data": data.hex(), "outcome": result}
        if result[0] == "ok" and has_surrogate(result[1]):
            case["outcome"] = ["row 3"]
        cases.append(case)
    return cases


def template_cases() -> list:
    b = BACKSLASH
    targeted = ["", "plain", b + b, b + "n", b + "a" + b + "b" + b + "f" + b + "r" + b + "t" + b + "v", b + "q",
                b + "T", b + "-", b + "0", b + "07", b + "012", b + "0129", b + "1", b + "9", b + "12", b + "123",
                b + "400", b + "777", b + "18", b + "g<0>", b + "g<00>", b + "g<1>", b + "g<name>", b + "g<1x>",
                b + "g<>", b + "g<0", b + "g", b + "g0", b + "g<-1>", b + "g<+0>", b + "g<٣>", b + "g<Δx>",
                b + "g<99999999999>", b + "g<1073741823>", b + "g<1073741822>", "trail" + b, b,
                "one" + chr(10) + "two " + b + "q", "a" + chr(10) + b + "g<x", "é" + b + "g<0>é",
                "C:" + b + "Temp", "# My " + b + "g<0> copy", b + "ö", b + chr(10)]
    alphabet = ["a", b, b, b, "g", "<", ">", "0", "1", "7", "8", "9", "n", "q", "-", "x", chr(10), "é", "_", "٣"]
    templates = targeted + ["".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 9))) for _ in range(700)]
    cases, seen = [], set()
    for template in templates:
        if template in seen:
            continue
        seen.add(template)
        cases.append({"template": template, "outcome": outcome(lambda: re.sub("x", template, "x"))})
    return cases


def text_cases() -> list:
    alphabet = ["a", "Δ", "1", "٣", "_", "-", " ", chr(10), chr(13), chr(11), chr(12), chr(28), chr(29), chr(30),
                chr(0x85), chr(0x2028), chr(0x2029), chr(13) + chr(10), "·", "ⅰ", "℘", "°", "ǅ", "̀"]
    texts = ["", "a", "_", "1a", "a1", "Δx", "x·", "·x", "℘", "ⅰ", "ǅ"]
    texts += ["".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 7))) for _ in range(400)]
    unique = list(dict.fromkeys(texts))
    return [{"text": text, "splitlines": text.splitlines(), "isidentifier": text.isidentifier()} for text in unique]


def main() -> None:
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "loads_bytes": loads_cases(), "templates": template_cases(),
               "texts": text_cases()}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + chr(10), encoding="utf-8",
                      newline=chr(10))
    for name in ("loads_bytes", "templates"):
        kinds = {}
        for case in vectors[name]:
            key = case["outcome"][0] if case["outcome"][0] in ("ok", "row 3") else case["outcome"][0] + " " + case["outcome"][1][:40]
            kinds[key] = kinds.get(key, 0) + 1
        print(name, len(vectors[name]), sorted(kinds.items(), key=lambda item: -item[1])[:14])
    print("texts", len(vectors["texts"]))


if __name__ == "__main__":
    main()
