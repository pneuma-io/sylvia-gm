"""Generate reference vectors for sylvia_base::pyyaml from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-base/tests/vectors/generate_pyyaml_vectors.py

PyYAML 6.0.3 with libyaml, as in the reference environment. Two loaders are recorded, because
Sylvia uses both: `yaml.SafeLoader` (pure Python: `yaml.safe_load`, the config files, the
Ruleset source loader) and `yaml.CSafeLoader` (libyaml's parser under the same constructor:
package runtimes). For each document and loader: the events, the composed node tree, and the
constructed value with Python's types, or the error's class, pieces and full text. For dumping:
`yaml.safe_dump(value, sort_keys=False, allow_unicode=True)` of every value that loaded, plus
crafted values. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import datetime
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import yaml
from yaml import nodes

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
OUTPUT = HERE / "pyyaml_vectors.json"
LOADERS = {"pure": yaml.SafeLoader, "c": yaml.CSafeLoader}

PLAIN_SCALARS = [
    "yes", "no", "Yes", "NO", "on", "Off", "ON", "y", "n", "Y", "N", "true", "True", "TRUE", "tRue",
    "false", "False", "~", "null", "Null", "NULL", "nULL", "", "0", "-0", "+1", "007", "08", "0b101",
    "-0b11", "0x1F", "0X1f", "0o17", "0_1", "1_000", "-1_000_000", "+12_3", "190:20:30", "1:30",
    "-1:30", "1:60", "3.14", ".5", "5.", "-.5", "1e3", "1.0e+3", "1.0e3", "1e-3", "6.8523015e+5",
    "685.230_15e+03", "190:20:30.15", ".inf", "-.Inf", "+.INF", ".NaN", ".nan", ".NAN", "inf", "nan",
    "1_0.5", "._5", "2026-11-21", "2026-1-2", "2026-11-21T12:00:00Z", "2026-11-21 12:00:00.5 -05:00",
    "2001-12-14t21:59:43.10-05:00", "2001-12-14 21:59:43.123456789", "2001-12-15 2:59:43.10",
    "2026-11-21T12:00:00+0530", "2026-13-01", "2026-02-30", "=", "<<", "9223372036854775807",
    "9223372036854775808", "-9223372036854775809", "1e400", "-1e400", "0.1", "1.5e308",
    "plain text", "a: b", "key", "multi word value", "#not comment", "100%", "@at", "`tick",
]


def scalar_documents() -> list[str]:
    docs = []
    for value in PLAIN_SCALARS:
        docs.append(f"value: {value}\n" if value else "value:\n")
        if value and value not in {"a: b", "#not comment", "@at", "`tick"}:
            docs.append(f"{value}: key\n")
            docs.append(f"- {value}\n")
            docs.append(f"[{value}]\n" if "," not in value else f"- {value}\n")
    return docs


STRUCTURES = [
    "", "\n", "# only a comment\n", "---\n", "--- \n...\n", "a: 1\n---\nb: 2\n", "a: 1\n...\n",
    "%YAML 1.1\n---\na: 1\n", "%YAML 1.2\n---\na: 1\n", "%TAG !e! tag:example.com,2000:\n---\n!e!x 1\n",
    "a:\n  b:\n    c: [1, 2, {d: e}]\n", "- a\n- - b\n  - c\n- d: e\n  f: g\n", "[a, [b, c], {d: e}]\n",
    "{a: 1, b: [2, 3], c: {d: null}}\n", "{a, b: c}\n", "[a: b, c]\n", "{ }\n", "[ ]\n", "a: []\nb: {}\n",
    "a: &x 1\nb: *x\n", "a: &x [1, 2]\nb: *x\nc: *x\n", "base: &b {x: 1, y: 2}\nd:\n  <<: *b\n  y: 3\n",
    "a: &a {x: 1}\nb: &b {y: 2}\nc:\n  <<: [*a, *b]\n  z: 3\n", "a: &a {x: 1}\nb: &b {x: 2}\nc:\n  <<: [*a, *b]\n",
    "c:\n  x: 0\n  <<: {x: 1, y: 2}\n", "<<: {a: 1}\n", "a: {<<: 5}\n", "a: {<<: [1]}\n",
    "a: &x\n  b: *x\n", "- &a [*a]\n", "a: *undefined\n", "a: &x 1\nb: &x 2\nc: *x\n",
    "a: 1\na: 2\n", "? [a, b]\n: c\n", "? {a: b}\n: c\n", "? a\n: b\n", "? |\n  block key\n: v\n",
    "null: 1\n~: 2\n", "1: a\n1.0: b\ntrue: c\n", "0x10: a\n16: b\n",
    "s: |\n  line1\n  line2\n", "s: |-\n  trimmed\n\n", "s: |+\n  kept\n\n", "s: >\n  folded\n  text\n\n  para\n",
    "s: >-\n  a\n   b\n  c\n", "s: |2\n    two extra\n", "s: |\n\n  leading blank\n",
    "s: \"esc \\x41 \\u00e9 \\U0001F3B2 \\N \\_ \\L \\P \\0 \\a \\b \\t \\n \\v \\f \\r \\e \\\" \\\\ \\/\"\n",
    "s: \"line\n  continued\"\n", "s: 'it''s'\n", "s: 'multi\n  line'\n", "s: plain\n  continued\n  more\n",
    "s: \"\\\n  joined\"\n", "s: !!str 123\n", "i: !!int '42'\n", "f: !!float '1'\n", "b: !!bool yes\n",
    "n: !!null ''\n", "bin: !!binary aGVsbG8=\n", "bin: !!binary |\n  aGVs\n  bG8=\n", "t: !!timestamp 2026-11-21\n",
    "o: !!omap [a: 1, b: 2]\n", "p: !!pairs [a: 1, a: 2]\n", "s: !!set {a, b}\n", "q: !!seq [1]\n", "m: !!map {a: 1}\n",
    "x: !custom 1\n", "x: !!python/object:os.system ls\n", "x: !!python/name:os.system ''\n",
    "x: !<tag:yaml.org,2002:str> 5\n", "a: 'unterminated\n", "a: \"bad \\q escape\"\n", "a: b: c\n",
    "a:\n\tb: 1\n", "a:\n  - 1\n - 2\n", "- a\nb: c\n", "a: [1, 2\n", "a: {b: 1\n", "key: value\n  bad: indent\n",
    "\ufeffa: 1\n", "a: 1\r\nb: 2\r\n", "a: 1\rb: 2\r", "a: caf\u00e9 \U0001F3B2 \u65e5\u672c\n",
    "a: \u0007bell\n", "a: \"\u0007\"\n", "a: \ufffe\n", "a: \x85next\n", "a: x\u2028y\n",
    "a: " + "x" * 2000 + "\n", ("k" * 1100) + ": v\n", "a: !!binary 'not base64!'\n", "a: !!int 'x'\n",
    "a: !!float 'x'\n", "a: !!timestamp 'x'\n", "a: !!omap [1]\n", "a: !!omap {a: 1}\n", "a: !!pairs {a: 1}\n",
    "&a a: 1\n", "a: !!bool maybe\n", "- [1, 2]: x\n", "a: - b\n", "- - - x\n", "-\n", "- \n-\n",
    "a: -\n", "a: |\n", "a: >\n", "key with spaces: v\n", "'quoted key': v\n", "\"dq key\": v\n",
    "a: 2026-11-21 12:00:00\nb: 2026-11-21t12:00:00.123z\n", "date: 2002-12-14\n",
]


def shipped_documents() -> list[tuple[str, str]]:
    files = subprocess.run(["git", "ls-files", "*.yaml", "*.yml"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    out = []
    for name in files:
        if name.startswith("sylviabench/"):
            continue
        out.append((name, (ROOT / name).read_text(encoding="utf-8")))
    return out


def recorded_configs() -> list[tuple[str, str]]:
    out = []
    for path in sorted((ROOT / "crates" / "sylvia-compare" / "recordings").glob("*/*.recording.json")):
        recording = json.loads(path.read_text(encoding="utf-8"))
        for entry in recording.get("files", []):
            if entry.get("path") == "config.yaml" and "text" in entry:
                out.append((f"{path.parent.name}/{path.stem}", entry["text"]))
    return out


TAB, BOM, BS = "\t", "\ufeff", "\\"


def libyaml_documents() -> list[str]:
    """Where libyaml's scanner and parser differ from PyYAML's: tabs, byte order marks, the end
    of the stream, directives, tags, escapes, plain-scalar and anchor edges, block-scalar
    indicators, and !!binary decoding."""
    docs = [
        # Tabs.
        "a: 1" + TAB + "\n", "a: b" + TAB + "c\n", "[a," + TAB + "b]\n", "{a:" + TAB + "b}\n", "a:" + TAB + "b\n",
        "- " + TAB + "x\n", "a: b" + TAB + "# c\n", "a: |\n" + TAB + "x\n", "a: |\n  x\n" + TAB + "\n",
        "a: >\n  x" + TAB + "y\n", "a:\n  b\n" + TAB + "c\n", "a: 'x" + TAB + "y'\n", "a: \"x\n" + TAB + "y\"\n",
        "a: x\n " + TAB + "y\n", "a: x\n  " + TAB + "y\n", "? a\n:" + TAB + "b\n", "%YAML" + TAB + "1.1\n---\na\n",
        "&x" + TAB + "a: 1\n", "a: !!str" + TAB + "x\n", TAB + "a: 1\n", "a:\n" + TAB + "- 1\n", "- a" + TAB + "\n- b\n",
        "a: [1," + TAB + "\n  2]\n", "a: 1 " + TAB + " # c\n", "a: " + TAB + "\"q\"\n",
        # Byte order marks.
        BOM + "a: 1\n", BOM + BOM + "a: 1\n", "a: 1\n" + BOM + "b: 2\n", "a: x" + BOM + "y\n", "- a\n" + BOM + "- b\n",
        BOM, BOM + "\n", BOM + "# c\n", "a:\n  " + BOM + "b: 1\n", BOM + "--- a\n",
        # The end of the stream without a final line break.
        "a", "a: 1", "a: 1   ", "- a\n- b", "a: |\n  x", "a: >-\n  x\n  y", "a: 1\n# c", "a:\n  b: 1\n  ", "[a, b]",
        "{a: b}", "a: 'x'", "a: \"x\"", "a: x\n  y", "---", "--- a", "...", "a\n...", "&a", "!!str", "? a", "a:", "-",
        "a: 1\r\nb: 2", "a: 1\rb", "a: 1\x85b: 2", "a: x\u2028b: 2", "a: |\n  x\r\n  y\r\n",
        # Directives.
        "%YAML 1.1\n--- a\n", "%YAML 1.2\n--- a\n", "%YAML 1.3\n--- a\n", "%YAML 2.0\n--- a\n", "%YAML 1.1\n%YAML 1.1\n--- a\n",
        "%YAML 01.01\n--- a\n", "%YAML 1234567890.1\n--- a\n", "%YAML 1\n--- a\n", "%YAML 1.x\n--- a\n", "%YAML\n--- a\n",
        "%FOO bar\n--- a\n", "%\n--- a\n", "%YAML 1.1 # c\n--- a\n", "%YAML 1.1 x\n--- a\n",
        "%TAG !e! tag:example.com,2000:\n--- !e!x 1\n", "%TAG !e! tag:e:\n%TAG !e! tag:f:\n--- a\n", "%TAG !! tag:x:\n--- !!y 1\n",
        "%TAG ! tag:x:\n--- !y 1\n", "%TAG e! tag:x:\n--- a\n", "%TAG !e tag:x:\n--- a\n", "%TAG !e!\n--- a\n",
        "%TAG !e! tag:%C3%A9:\n--- !e!x 1\n", "%TAG !e! tag:%C3%28:\n--- a\n", "%TAG !e! tag:%C0%80:\n--- a\n",
        "%TAG !e! tag:x:\n--- !f!y 1\n", "%TAG !e! tag:x: y\n--- a\n", "--- a\n%YAML 1.1\n--- b\n", "%YAML 1.1\na\n",
        # Tags.
        "a: !e%21x 1\n", "a: !<tag:x%C3%A9> 1\n", "a: !<%C0%80> 1\n", "a: !%00x 1\n", "a: ! x\n", "a: !! x\n", "a: !a!b 1\n",
        "a: !<tag:x 1\n", "a: !<> 1\n", "a: !x,y 1\n", "[!x,y]\n", "{!x a: b}\n", "a: !%zz 1\n", "a: !%C3 1\n", "a: !%FF 1\n",
        "a: ! [1]\n", "a: ! {b: 1}\n", "a: ! 1\n", "a: !!int 1\n", "a: !<tag:yaml.org,2002:int> 1\n", "a: &x !!str 1\n",
        "a: !!str &x 1\n", "a: &x\n", "a: !!str\n", "- &x\n- *x\n", "a: !x\n  b: 1\n", "!x\n- 1\n",
        # Escapes.
        'a: "' + BS + 'x4"\n', 'a: "' + BS + 'u00zz"\n', 'a: "x' + BS + 'q"\n', 'a: "' + BS + 'U0001F600"\n',
        'a: "' + BS + 'UFFFFFFFF"\n', 'a: "' + BS + 'uD800"\n', 'a: "' + BS + ' x"\n', 'a: "' + BS + '/"\n',
        'a: "' + BS + TAB + 'x"\n', 'a: "' + BS + 'x41' + BS + 'u00e9' + BS + 'N' + BS + '_' + BS + 'L' + BS + 'P"\n',
        'a: "' + BS + '\u00e9"\n', "a: 'it''s'\n", "a: 'x\n\n  y'\n", 'a: "x\n\n  y"\n', 'a: "x' + BS + '\n  y"\n',
        'a: "x\n---\n"\n', "a: 'x\n...\n'\n", "a: 'x", 'a: "x  \n  y"\n', 'a: "  x  "\n',
        # Plain scalars, keys and flow edges.
        "a: b:c\n", "[a:b]\n", "[a:,b]\n", "{a: b:c}\n", "[a:[b]]\n", "{a:}\n", "- -x\n", "?x\n", ":x\n", "a: -\n",
        "[- a]\n", "[a, - b]\n", "a: [b, c]d\n", "{? a: b}\n", "[? a : b]\n", "[a: b, c: d]\n", "{a, b}\n", "[a]b\n",
        "a: 1 #c\n", "a: 1#c\n", "a\nb: c\n", "a b\n  c: d\n", "a: x\n  - y\n", "a:\n- b\n- c\n", "a:\n  - b\n - c\n",
        "x" * 1030 + ": v\n", "? " + "x" * 1030 + "\n: v\n", "[" + "x" * 1030 + ": v]\n", "a: \u00e9\u00e9 b\n",
        "- a\n  - b\n", "a: b\n c\n", "a: b\n  c: d\n", "{a: 1\n}\n", "[\n1\n]\n", "{\n? a\n: b\n}\n",
        # Anchors and aliases.
        "&a\n", "& a\n", "&a: b\n", "a: *b?\n", "a: &b@\n", "a: *\n", "&a [*a]\n", "a: &x 1\nb: *x\n",
        # Block scalars.
        "a: |0\n  x\n", "a: |-1\n  x\n", "a: |10\n  x\n", "a: |+ # c\n  x\n\n", "a: |x\n  x\n", "a: |2-\n   x\n",
        "a: >\n  x\n   y\n  z\n", "a: >\n\n  x\n\n", "a: |\n  x\n\n\n", "a: |+\n  x\n\n\n", "a: >-\n  x\n\n  y\n",
        "a: |\n x\n  y\n", "a: |\n  x\n y\n", "- |\n  x\n- y\n", "a: |\n\n\n", "a: >\n   \n  x\n", "a: |1\n  x\n",
        # Documents.
        "--- a\n--- b\n", "...\n...\n", "---\n...\n", "a\n---\n", "--- |\n  x\n--- y\n", "--- >\na\n", "---a\n",
        "a: 1\n--- \n", "# c\n---\n# d\n", "--- # c\na\n",
        # !!binary.
        "a: !!binary \"aGVs bG8=\"\n", "a: !!binary 'a=b'\n", "a: !!binary 'YQ'\n", "a: !!binary 'Y'\n",
        "a: !!binary ''\n", "a: !!binary '===='\n", "a: !!binary 'ab==cd'\n", "a: !!binary '\u00e9\u00e9z'\n",
        "a: !!binary 'a\u00e9b\u00e9'\n", "a: !!binary 'YWJj\nZGVm'\n", "a: !!binary 'Y=Q=='\n", "a: !!binary 'YQ=x'\n",
        "a: !!binary 'Y W J j'\n", "a: !!binary '!!!!YWJj'\n", "a: !!binary 'YWJjZA'\n", "a: !!binary 'YWJjZA='\n",
        "a: !!binary [1]\n", "a: !!binary\n",
        # Constructor edges.
        "a: !!timestamp 2026-13-01 25:00:00 +25\n", "a: !!timestamp 2026-12-01 1:00:00 -24:00\n",
        "a: !!timestamp 2026-02-29\n", "a: !!timestamp 2024-02-29 23:59:60\n", "a: !!int 0x\u0663\n", "a: !!int \u0663\u0663\n",
        "a: !!int -9223372036854775808\n", "a: !!int 0b\n", "a: !!int -\n", "a: !!int ''\n", "a: !!int 1:2:3:4:5:6:7:8:9:10:11\n",
        "a: !!float 1:30.5\n", "a: !!float ''\n", "a: !!float nan\n", "{.nan: 1, .nan: 2}\n", "{!!float nan: 1, !!float nan: 2}\n",
        "{1: a, 1.0: b, true: c}\n", "{9007199254740993: a, 9007199254740992.0: b}\n", "a: !!set [1]\n", "a: !!omap {b: 1}\n",
        "a: !!pairs [b: 1, [c]]\n", "a: !!omap [b: 1, {c: 1, d: 2}]\n", "a: &x !!seq [*x]\n", "&x {a: *x}\n",
        "a: {<<: *b}\n", "b: &b {x: 1}\na: {<<: [*b, 1]}\n", "a: &x {<<: *x}\n", "? [a]\n: 1\n", "? {a: 1}\n: 2\n",
        "? !!set {a}\n: 1\n", "a: !!null x\n", "a: !!bool [1]\n", "a: !!str {=: x}\n", "a: !!str {b: x}\n",
    ]
    big = "k: v\n" * 4000
    docs += [
        # Over 16 KiB: libyaml checks characters a 16 KiB chunk at a time.
        "a: b: c\n" + big + "x: \x07\n",
        "x: \x07\n" + big + "a: b: c\n",
        big + "a: b: c\n" + big + "x: \x07\n",
        "a: b: c\n" + "k: " + "v" * 16370 + "\x07\n",
        "k: " + "\u00e9" * 8200 + "\n",
        "k: " + "\u00e9" * 8189 + "\x07\n",
        ("k: v\r\n" * 2731) + "z: 1\r\n",
        "a: b: c\n" + "k: " + "x" * 16372 + "\U0001F3B2\x07\n",
    ]
    return docs


FRAGMENTS = [
    "a", "b c", "'q'", "\"d\"", "1", "-2", "3.5", "~", "yes", "[x, y]", "{k: v}", "&n 1", "*n", "|\n  t\n",
    ">\n  f\n", "!!str 7", "null", "2026-01-01", "0x1A", "1:2", ".inf", "", "- e", "x: y", "'a''b'",
]


def fuzz_documents(count: int) -> list[str]:
    rng = random.Random(20261121)
    docs = []
    for _ in range(count):
        lines = []
        depth = 0
        for _ in range(rng.randint(1, 6)):
            indent = "  " * depth
            key = rng.choice(["k", "key", "a b", "'x'", "1", "~", "-"])
            value = rng.choice(FRAGMENTS)
            form = rng.random()
            if form < 0.5:
                lines.append(f"{indent}{key}: {value}")
            elif form < 0.7:
                lines.append(f"{indent}- {value}")
            elif form < 0.85:
                lines.append(f"{indent}{key}:")
                depth = min(depth + 1, 3)
            else:
                depth = max(depth - 1, 0)
                lines.append(f"{indent}{value}")
        docs.append("\n".join(lines) + ("\n" if rng.random() < 0.9 else ""))
    return docs


def mark(m) -> list | None:
    return None if m is None else [m.line, m.column, m.index]


def event_record(event) -> list:
    kind = type(event).__name__
    record = [kind, mark(event.start_mark), mark(event.end_mark)]
    if isinstance(event, yaml.ScalarEvent):
        record += [event.anchor, event.tag, list(event.implicit), event.value, event.style]
    elif isinstance(event, (yaml.SequenceStartEvent, yaml.MappingStartEvent)):
        record += [event.anchor, event.tag, event.implicit, event.flow_style]
    elif isinstance(event, yaml.AliasEvent):
        record += [event.anchor]
    elif isinstance(event, yaml.DocumentStartEvent):
        record += [event.explicit, list(event.version) if event.version else None,
                   sorted(event.tags.items()) if event.tags else None]
    elif isinstance(event, yaml.DocumentEndEvent):
        record += [event.explicit]
    return record


def node_record(node, ids: dict) -> list:
    key = id(node)
    if key in ids:
        return ["ref", ids[key]]
    ids[key] = len(ids)
    base = [ids[key], node.tag, mark(node.start_mark), mark(node.end_mark)]
    if isinstance(node, nodes.ScalarNode):
        return ["scalar", *base, node.value, node.style]
    if isinstance(node, nodes.SequenceNode):
        return ["seq", *base, node.flow_style, [node_record(item, ids) for item in node.value]]
    return ["map", *base, node.flow_style,
            [[node_record(k, ids), node_record(v, ids)] for k, v in node.value]]


def value_record(value, ids: dict, set_order: bool = False):
    """Python's value with its types, cycles shown as references. Set items are sorted, or with
    `set_order` kept in the order Python iterates them (the order safe_dump writes)."""
    if isinstance(value, (list, dict, set)) and id(value) in ids:
        return ["ref", ids[id(value)]]
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", "nan" if math.isnan(value) else repr(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, datetime.datetime):
        return ["datetime", value.isoformat(), value.tzinfo is not None]
    if isinstance(value, datetime.date):
        return ["date", value.isoformat()]
    if isinstance(value, tuple):
        return ["tuple", [value_record(item, ids, set_order) for item in value]]
    if isinstance(value, list):
        ids[id(value)] = len(ids)
        return ["list", ids[id(value)], [value_record(item, ids, set_order) for item in value]]
    if isinstance(value, dict):
        ids[id(value)] = len(ids)
        return ["dict", ids[id(value)], [[value_record(k, ids, set_order), value_record(v, ids, set_order)] for k, v in value.items()]]
    if isinstance(value, set):
        ids[id(value)] = len(ids)
        items = [value_record(item, ids, set_order) for item in value]
        return ["set", ids[id(value)], items if set_order else sorted(items, key=json.dumps)]
    raise TypeError(type(value))


def error_record(error: Exception) -> dict:
    record = {"class": type(error).__name__, "text": str(error)}
    for name in ("context", "problem", "note"):
        record[name] = getattr(error, name, None)
    for name in ("context_mark", "problem_mark"):
        record[name] = mark(getattr(error, name, None))
    return record


def load_record(text: str, loader) -> dict:
    out = {}
    try:
        out["events"] = [event_record(e) for e in yaml.parse(text, Loader=loader)]
    except Exception as error:  # noqa: BLE001
        out["events_error"] = error_record(error)
    try:
        node = yaml.compose(text, Loader=loader)
        out["node"] = None if node is None else node_record(node, {})
    except Exception as error:  # noqa: BLE001
        out["compose_error"] = error_record(error)
    try:
        value = yaml.load(text, Loader=loader)
        out["value"] = value_record(value, {})
    except Exception as error:  # noqa: BLE001 - PyYAML also raises AttributeError and others
        out["error"] = error_record(error)
    if any(0xD800 <= ord(c) <= 0xDFFF for c in json.dumps(out, ensure_ascii=False)):
        # Python built a str holding a lone surrogate, which Rust's str cannot hold: the port
        # refuses it (differences.md row 3). Recorded as that fact rather than the value.
        return {"lone_surrogate": True}
    return out


class UniqueKeysLoader(yaml.CSafeLoader):
    """The `flatten_mapping` override of `application/package_runtime._UniqueRuntimeLoader`,
    without its graph bounds (another lane's port), to prove the Rust hook API."""

    def flatten_mapping(self, node):
        if not getattr(node, "_unique_keys_checked", False):
            seen = set()
            for key_node, _value_node in node.value:
                key = "<<" if key_node.tag == "tag:yaml.org,2002:merge" else self.construct_object(key_node)
                if not isinstance(key, str) or key in seen:
                    raise ValueError("Runtime fields must be unique text keys")
                seen.add(key)
            node._unique_keys_checked = True
        super().flatten_mapping(node)


class TextKeysLoader(yaml.SafeLoader):
    """The `construct_mapping` override of `builder/_ruleset_source._SourceLoader`, without its
    graph bounds."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key, value in self.construct_pairs(node, deep=deep):
            if not isinstance(key, str) or key in result:
                raise ValueError("Ruleset source requires unique text field names")
            result[key] = value
        return result


class FirstDocumentNodeCount(yaml.SafeLoader):
    """A `construct_document` override that inspects the graph first, as the bounded loaders do."""

    def construct_document(self, node):
        count, stack, seen = 0, [node], set()
        while stack:
            current = stack.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            count += 1
            if isinstance(current, nodes.SequenceNode):
                stack.extend(current.value)
            elif isinstance(current, nodes.MappingNode):
                for key, value in current.value:
                    stack.extend((key, value))
        if count > 12:
            raise ValueError("too many nodes")
        return super().construct_document(node)


HOOK_CASES = [
    "a: 1\nb: 2\n", "a: 1\na: 2\n", "1: a\n", "a: {b: 1, b: 2}\n", "base: &b {x: 1}\nd:\n  <<: *b\n  x: 2\n",
    "base: &b {x: 1}\nd:\n  <<: *b\n  <<: *b\n", "d:\n  <<: [{x: 1}, {y: 2}]\n  z: 3\n", "a: !!set {x, y}\n",
    "a: !!set {x, x}\n", "? [k]\n: v\n", "a: &x {b: 1}\nc: *x\nd: *x\n", "- {a: 1, a: 2}\n", "{a: 1, 'a': 2}\n",
    "a: !!omap [b: 1, b: 2]\n", "a: &r\n  b: *r\n", "a: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]\n", "a: {=: 1}\n",
    "~: 1\n", "true: 1\n", "a: !!str {=: x}\n", "<<: {a: 1}\na: 2\n",
]


def hook_vectors() -> list:
    out = []
    for text in HOOK_CASES:
        case = {"text": text}
        for name, loader in (("unique_keys", UniqueKeysLoader), ("text_keys", TextKeysLoader), ("node_count", FirstDocumentNodeCount)):
            try:
                case[name] = {"value": value_record(yaml.load(text, Loader=loader), {})}
            except Exception as error:  # noqa: BLE001
                case[name] = {"error": error_record(error)}
        out.append(case)
    return out


def dump_values(loaded: list) -> list:
    crafted = [
        None, True, False, 0, -1, 2**63 - 1, 1.0, -0.0, 0.1, 1e16, 1e-5, float("inf"), float("-inf"),
        float("nan"), "", " ", "plain", "yes", "No", "null", "~", "123", "1.5", "0x1F", "1:30", "2026-11-21",
        "a: b", "- item", "#hash", "trailing ", " leading", "multi\nline", "multi\nline\n", "trail\n\n",
        "tab\there", "quote ' and \" both", "caf\u00e9 \U0001F3B2", "\u0007bell", "\x85", "\u2028",
        "x" * 200, "word " * 40, ("word " * 40).strip(), "long-unbroken-" * 10, "@start", "`tick", "!bang",
        "%pct", "&amp", "*star", "|pipe", ">gt", "{brace", "[bracket", ",comma", "?q", "a#b", "a #b",
        b"\x00\x01binary", datetime.date(2026, 11, 21), datetime.datetime(2026, 11, 21, 12, 30, 15, 250000),
        [], {}, [[]], [{}], {"a": []}, {"a": {}}, [1, [2, [3]]], {"k": [1, 2], "m": {"n": None}},
        [{"a": 1, "b": 2}, {"c": [3, {"d": 4}]}], {1: "int key", None: "none key", True: "bool key", 1.5: "f"},
        {"nested": {"deeper": {"deepest": "x" * 100}}}, {"list of strings": ["a b", "c: d", "", "yes"]},
        {"providers": {"x": {"enabled": True, "models": [{"id": "m", "context_window": None}]}}},
    ]
    values = crafted + [item for item in loaded if item is not None]
    # A loaded lone surrogate cannot be held in Rust (differences.md row 3).
    values = [v for v in values if not any(0xD800 <= ord(c) <= 0xDFFF for c in json.dumps(value_record(v, {}), ensure_ascii=False))]
    out = []
    for value in values:
        try:
            text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
        except Exception as error:  # noqa: BLE001
            out.append({"value": value_record(value, {}, True), "error": error_record(error)})
            continue
        out.append({"value": value_record(value, {}, True), "text": text})
    return out


def main() -> int:
    named = ([("shipped:" + name, text) for name, text in shipped_documents()]
             + [("config:" + name, text) for name, text in recorded_configs()])
    texts = [(f"scalar:{i}", t) for i, t in enumerate(scalar_documents())]
    texts += [(f"structure:{i}", t) for i, t in enumerate(STRUCTURES)]
    texts += [(f"libyaml:{i}", t) for i, t in enumerate(libyaml_documents())]
    texts += [(f"fuzz:{i}", t) for i, t in enumerate(fuzz_documents(400))]
    cases = []
    loaded_values = []
    for name, text in named + texts:
        case = {"name": name, "text": text}
        for loader_name, loader in LOADERS.items():
            case[loader_name] = load_record(text, loader)
        cases.append(case)
        try:
            value = yaml.load(text, Loader=yaml.SafeLoader)
        except Exception:  # noqa: BLE001
            continue
        if not name.startswith("fuzz") or len(loaded_values) < 600:
            loaded_values.append(value)
    document = {
        "python": sys.version.split()[0], "pyyaml": yaml.__version__, "libyaml": yaml.__with_libyaml__,
        "cases": cases, "dumps": dump_values(loaded_values), "hooks": hook_vectors(),
        # `yaml.load(bytes)` (campaign_rules_update.py): the same document, marks named "<byte string>".
        "byte_inputs": [
            {"text": text, **{name: load_record(text.encode("utf-8"), loader) for name, loader in LOADERS.items()}}
            for text in ("a: 1\n", "a: b: c\n", "a: 'x", "a: \x07\n", "﻿a: [1,\n", "k: é\nx: *y\n")
        ],
        # differences.md: a date reached twice is one object in Python, so safe_dump anchors it.
        "date_alias": [
            {"text": text, "python": yaml.safe_dump(yaml.safe_load(text), sort_keys=False, allow_unicode=True)}
            for text in ("a: &d 2026-01-01\nb: *d\n", "- &t 2026-01-01 12:00:00\n- *t\n- *t\n")
        ],
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    differing = sum(1 for c in cases if json.dumps(c["pure"].get("value"), sort_keys=True)
                    != json.dumps(c["c"].get("value"), sort_keys=True)
                    or ("error" in c["pure"]) != ("error" in c["c"]))
    print(f"{len(cases)} documents ({differing} where the loaders differ), "
          f"{len(document['dumps'])} dumps -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
