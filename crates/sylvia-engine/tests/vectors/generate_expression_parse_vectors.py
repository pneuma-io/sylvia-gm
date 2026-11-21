"""Generate reference vectors for sylvia_engine::expr (tokenizer and parser) from the snapshot.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_expression_parse_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in, so the Rust tests need no Python.
Reference tooling for the port, retired with the Python source.

Each case is a text and either the AST `parse_expression` builds (dumped as JSON below) or the
`ExpressionError` message. The corpus:

- every expression the schema validator parses while validating the shipped SRD Lite and
  Riichi Mahjong runtime rulesets;
- every other string in those rulesets: the effect executor's `_resolve_value` tries to parse
  any string and falls back to the literal on `ExpressionError`, so sentences, identifiers and
  dice notation must succeed or fail exactly as in Python;
- hand-written edge cases (limits, keywords and word boundaries, quotes, forbidden names,
  nesting depth, integers beyond 64 bits);
- seeded random token soup.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import zipfile
from pathlib import Path

import yaml

import sylvia_gm.runtime._schema_validator as schema_validator
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime._exceptions import ExpressionError
from sylvia_gm.runtime._expression_parser import (
    BinaryOpNode,
    FieldAccessNode,
    FunctionCallNode,
    LiteralNode,
    UnaryOpNode,
    parse_expression,
)

OUTPUT = Path(__file__).with_name("expression_parse_vectors.json")
# Groups whose ASTs are stored as hashes; the others keep the whole tree for readable failures.
HASHED_GROUPS = {"validated", "ruleset_strings"}
ROOT = Path(__file__).resolve().parents[4]


def dump(node) -> list:
    """The AST as compact JSON arrays: ["i", digits], ["f", repr], ["s", text], ["b", flag],
    ["n"] for literals; ["F", [name, index or null], ...]; ["U", op, x]; ["B", op, left, right];
    ["C", name, [args]]."""
    if isinstance(node, LiteralNode):
        value = node.value
        if value is None:
            return ["n"]
        if isinstance(value, bool):
            return ["b", value]
        if isinstance(value, int):
            return ["i", str(value)]
        if isinstance(value, float):
            return ["f", repr(value)]
        return ["s", value]
    if isinstance(node, FieldAccessNode):
        return ["F", *[[name, None if index is None else dump(index)] for name, index in node.segments]]
    if isinstance(node, UnaryOpNode):
        return ["U", node.operator, dump(node.operand)]
    if isinstance(node, BinaryOpNode):
        return ["B", node.operator, dump(node.left), dump(node.right)]
    if isinstance(node, FunctionCallNode):
        return ["C", node.name, [dump(arg) for arg in node.args]]
    raise AssertionError(type(node))


def case(text: str, hashed: bool = False) -> dict:
    """The AST (or, for the two large groups, the first 16 hex digits of the SHA-256 of its
    canonical JSON bytes), or the error. In the message, `'<text>'` is written as `'\\0'` to keep the file
    small; the test puts the text back."""
    try:
        tree = dump(parse_expression(text))
        if hashed:
            canonical = canonical_json_bytes(tree)
            return {"text": text, "ast_sha256": hashlib.sha256(canonical).hexdigest()[:16]}
        return {"text": text, "ast": tree}
    except ExpressionError as error:
        message = str(error).replace(f"'{text}'", "'\\0'") if text else str(error)
        return {"text": text, "error": message, "class": type(error).__name__}


def ruleset_sources() -> dict[str, str]:
    return {
        "srd-lite": (ROOT / "rulesets/srd-lite/runtime/ruleset.yaml").read_text(encoding="utf-8"),
        "riichi": zipfile.ZipFile(ROOT / "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package")
        .read("packages/pneuma.riichi-mahjong/runtime/ruleset.yaml").decode("utf-8"),
    }


def validated_expressions(sources: dict[str, str]) -> list[str]:
    seen: dict[str, None] = {}
    original = schema_validator.parse_expression

    def spy(expression):
        seen.setdefault(expression, None)
        return original(expression)

    schema_validator.parse_expression = spy
    schema_validator._validate_expression.cache_clear()
    try:
        for text in sources.values():
            assert schema_validator.validate_ruleset(text) == []
    finally:
        schema_validator.parse_expression = original
    return list(seen)


def ruleset_strings(sources: dict[str, str]) -> list[str]:
    found: dict[str, None] = {}

    def walk(value) -> None:
        if isinstance(value, str):
            found.setdefault(value, None)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for text in sources.values():
        walk(yaml.safe_load(text))
    return list(found)


EDGES = [
    "", " ", "1", "1.5", "1.", ".5", "007", "1.50", "9223372036854775807", "9223372036854775808",
    "99999999999999999999 + 1", "-9223372036854775808", "1" * 500, "1" * 501, "'" + "a" * 200 + "'",
    "'" + "a" * 201 + "'", '"' + "é" * 200 + '"', '"' + "é" * 201 + '"', "'unterminated", '"mixed\'',
    "'it''s'", "true", "false", "null", "True", "NULL", "true_", "truex", "true1", "trueé", "true.x",
    "nullé", "and", "not", "in", "contains", "a and b or c", "not a == b", "a not in b", "a not b",
    "a in b in c", "1 < 2 < 3", "a == b == c", "-x", "--x", "not not x", "- - 1", "2 ** 3 ** 2",
    "-2 ** 2", "a.b.c", "a[0]", "a[0][1]", "a[0].b[1]", "a['k']", "a.0", "a..b", "a.", "a[", "a[]",
    "max(1, 2)", "max()", "max(1,)", "max(,1)", "foo(1)", "len(x) > 0", "partitions(a, b, c)",
    "max", "max.x", "(1)", "((1))", "(1", "1)", "()", "[1]", "{1}", "1 2", "a b", "a,b", "a=b", "a = b",
    "a != b", "a !b", "!a", "a / b", "a // b", "a % b", "a %% b", "a *** b", "a <> b", "a >== b",
    "__import__", "eval(1)", "_", "__", "___", "____", "_x", "type", "type.x", "x.type", "object",
    "é", "aé", "x y", "x\x0by", "x\ty\nz\r", "\U0001f3b2", "a​b", "1e5", "0x10", "1_000",
    "'a' + 'b'", "'\\n'", "a[b[c[d]]]", "f", "abs(-1)", "floor(1.5) + ceil(2.5)",
    "(" * 9 + "1" + ")" * 9, "(" * 10 + "1" + ")" * 10, "(" * 11 + "1" + ")" * 11,
    "-" * 25 + "1", "not " * 25 + "x", "a" + "[0]" * 12, "max(" * 9 + "1" + ")" * 9,
    "max(" * 10 + "1" + ")" * 10, "x" + ".y" * 200,
]


def token_soup(rng: random.Random) -> list[str]:
    pieces = [
        "a", "b", "_x", "actor", "state.phase", "1", "2.5", "0", "'s'", '"t"', "true", "false", "null",
        "and", "or", "not", "in", "contains", "+", "-", "*", "//", "%", "**", "==", "!=", ">=", "<=", ">",
        "<", ".", "(", ")", "[", "]", ",", " ", "max", "len", "portion", "é", "!", "=", "/", "'", '"',
        "\t", "type", "_",
    ]
    out = []
    for _ in range(4000):
        out.append("".join(rng.choice(pieces) for _ in range(rng.randint(1, 12))))
    for _ in range(1500):
        out.append(" ".join(rng.choice(pieces) for _ in range(rng.randint(1, 10))))
    return out


def main() -> int:
    sources = ruleset_sources()
    validated = validated_expressions(sources)
    strings = [text for text in ruleset_strings(sources) if text not in set(validated)]
    soup = token_soup(random.Random(5050))
    groups = {"validated": validated, "ruleset_strings": strings, "edges": EDGES, "soup": soup}
    document = {
        "python": sys.version.split()[0],
        "groups": {
            name: [case(text, hashed=name in HASHED_GROUPS) for text in texts] for name, texts in groups.items()
        },
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True) + "\n").encode("ascii"))
    summary = ", ".join(
        f"{name} {len(cases)} ({sum('error' not in c for c in cases)} parse)" for name, cases in document["groups"].items()
    )
    print(f"{summary} -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
