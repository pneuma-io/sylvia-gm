"""List the places in the Python snapshot where a Rust port can silently differ.

Reference tooling for the port; deleted with the Python source. Run from the repository root:

    .venv/Scripts/python.exe docs/rust-port/tools/site_lists.py

Writes docs/rust-port/sites/*.md, one file per hazard, grouped by lane:

- hash-sites.md        every hashlib / hmac call, with what feeds it
- json-encodings.md    every json.dumps call and the encoding its options select
- regex-sites.md       every regular expression, flagged when Rust's `regex` crate cannot run it
- unicode-sites.md     case folding, normalisation and character-class calls
- length-limits.md     len() compared with a limit (Python counts code points, Rust counts bytes)
- yaml-sites.md        every YAML load and dump
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from port_ledger import APP, LANES, ROOT, git_files, lane_of  # noqa: E402

OUT = ROOT / "docs" / "rust-port" / "sites"

# Constructs Rust's `regex` crate rejects; `fancy-regex` accepts all of them.
REGEX_FEATURES = (
    ("lookahead", re.compile(r"\(\?[=!]")),
    ("lookbehind", re.compile(r"\(\?<[=!]")),
    ("backreference", re.compile(r"\\[1-9]|\(\?P=")),
    ("atomic/possessive", re.compile(r"\(\?>|[*+?]\+")),
    ("conditional", re.compile(r"\(\?\(")),
)
REGEX_CALLS = {"compile", "match", "search", "fullmatch", "sub", "subn", "split", "findall",
               "finditer"}
UNICODE_METHODS = {"casefold", "isidentifier", "isalnum", "isalpha", "isdigit", "isdecimal",
                   "isnumeric", "isspace", "isprintable", "isupper", "islower", "istitle",
                   "title", "capitalize", "swapcase"}


def source_line(lines: list[str], node: ast.AST) -> str:
    text = " ".join(part.strip() for part in lines[node.lineno - 1:getattr(node, "end_lineno", node.lineno)])
    text = text.replace("|", "\\|").replace("`", "'")
    return text if len(text) <= 150 else text[:147] + "..."


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def literal(node: ast.expr | None) -> object:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return "<expression>"


def json_encoding(call: ast.Call) -> str:
    sort_keys = literal(keyword(call, "sort_keys"))
    separators = literal(keyword(call, "separators"))
    ensure_ascii = literal(keyword(call, "ensure_ascii"))
    indent = literal(keyword(call, "indent"))
    extras = [item.arg for item in call.keywords
              if item.arg in {"default", "cls", "allow_nan"} or item.arg is None]
    compact = separators == (",", ":")
    if any(value == "<expression>" for value in (sort_keys, separators, ensure_ascii, indent)):
        name = "computed options"
    elif sort_keys is True and compact and ensure_ascii is False and indent is None:
        name = "Dump::CANONICAL"
    elif compact and indent is None:
        name = "Dump::WIRE"
        if ensure_ascii is False:
            name += ".ascii(false)"
        if sort_keys is True:
            name += ".sorted(true)"
    else:
        name = "Dump::INDENT2" if indent == 2 else ("Dump::DEFAULT" if indent is None else f"indent={indent}")
        if separators not in (None, (",", ": ")) and not compact:
            name += f" separators={separators}"
        if ensure_ascii is False:
            name += ".ascii(false)"
        if sort_keys is True:
            name += ".sorted(true)"
    if extras:
        name += " + " + ", ".join(f"{extra or '**kwargs'}=" for extra in extras)
    return name


class Scan(ast.NodeVisitor):
    def __init__(self, path: str, lines: list[str]) -> None:
        self.path, self.lines = path, lines
        self.scope: list[str] = []
        self.found: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)

    def add(self, topic: str, node: ast.AST, detail: str) -> None:
        where = ".".join(self.scope) or "<module>"
        self.found[topic].append((node.lineno, where, detail, source_line(self.lines, node)))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]
    visit_ClassDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "pattern"
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                self.regex(value, value.value, "JSON Schema pattern")
        self.generic_visit(node)

    def regex(self, node: ast.AST, pattern: str, kind: str) -> None:
        flags = [name for name, test in REGEX_FEATURES if test.search(pattern)]
        self.add("regex", node, f"{kind}; " + ("NEEDS fancy-regex: " + ", ".join(flags) if flags else "plain"))

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted(node.func)
        head, _, tail = name.rpartition(".")
        if head == "hashlib" or name in {"hmac.new", "hmac.digest", "hmac.compare_digest"}:
            self.add("hash", node, name)
        elif name == "json.dumps" or name == "json.dump":
            self.add("json", node, json_encoding(node))
        elif head == "re" and tail in REGEX_CALLS:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self.regex(node, first.value, f"re.{tail}")
            else:
                self.add("regex", node, f"re.{tail}; pattern built at run time, check by hand")
        elif head == "unicodedata" or (head and tail in UNICODE_METHODS):
            self.add("unicode", node, name if head == "unicodedata" else f".{tail}()")
        elif head == "yaml":
            self.add("yaml", node, name)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        sides = [node.left, *node.comparators]
        has_len = any(isinstance(side, ast.Call) and dotted(side.func) == "len" for side in sides)
        limit = any(
            (isinstance(side, ast.Constant) and isinstance(side.value, int) and side.value >= 8)
            or (isinstance(side, (ast.Name, ast.Attribute)) and dotted(side).split(".")[-1].isupper())
            for side in sides
        )
        if has_len and limit and any(isinstance(op, (ast.Gt, ast.GtE, ast.Lt, ast.LtE)) for op in node.ops):
            self.add("length", node, "len() against a limit")
        self.generic_visit(node)


TOPICS = {
    "hash": ("hash-sites.md", "Hash and HMAC call sites",
             "Every `hashlib` and `hmac` call. For each one the port must reproduce the exact "
             "bytes fed to the hash: find the encoding in the same function (often a "
             "`json.dumps` listed in json-encodings.md) and add a reference vector."),
    "json": ("json-encodings.md", "JSON encoding call sites",
             "Every `json.dumps` call and the `sylvia_base::pyjson::Dump` options that reproduce "
             "it. `+ default=` means non-JSON values are converted on the way out; port the "
             "conversion too."),
    "regex": ("regex-sites.md", "Regular expressions",
              "Every regular expression. Rows marked NEEDS fancy-regex use constructs the "
              "`regex` crate rejects. Python's `re` and Rust engines also differ on `\\d`, "
              "`\\w`, `\\s` and `\\b` (Unicode-aware in both by default, but not the same "
              "tables) and on `$` (Python's also matches before a final newline). Test every "
              "pattern against Python's verdicts."),
    "unicode": ("unicode-sites.md", "Unicode-dependent calls",
                "Case folding, normalisation and character-class calls. Results depend on the "
                "Unicode tables of the interpreter (CPython 3.11 has Unicode 14.0, 3.12 has "
                "15.0). The reference environment for the port is CPython 3.12.11; pin the Rust "
                "tables to Unicode 15.0 and include non-ASCII names in the vectors."),
    "length": ("length-limits.md", "Length limits",
               "`len()` compared with a limit. `len(str)` counts code points; Rust's "
               "`str::len` counts UTF-8 bytes. Use `chars().count()` wherever Python measured "
               "a `str`, and keep `len()` only where Python measured `bytes`."),
    "yaml": ("yaml-sites.md", "YAML loads and dumps",
             "Every PyYAML call. PyYAML (YAML 1.1) reads `yes`, `no`, `on`, `off`, `~`, octal "
             "and sexagesimal numbers and dates as non-strings; most Rust YAML crates follow "
             "YAML 1.2 and do not. Dumps must reproduce PyYAML's quoting and key order."),
}


def main() -> int:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    if len(sys.argv) > 1:
        commit = sys.argv[1]
    by_topic: dict[str, dict[int, list[tuple[str, int, str, str, str]]]] = {
        topic: defaultdict(list) for topic in TOPICS}
    for path in git_files("src/sylvia_gm/**/*.py") + git_files("src/sylvia_gm/*.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        scan = Scan(path, text.splitlines())
        scan.visit(ast.parse(text))
        lane, _target = lane_of(path)
        for topic, rows in scan.found.items():
            for line, where, detail, code in rows:
                by_topic[topic][lane].append((path, line, where, detail, code))
    OUT.mkdir(parents=True, exist_ok=True)
    for topic, (filename, title, intro) in TOPICS.items():
        lanes = by_topic[topic]
        total = sum(len(rows) for rows in lanes.values())
        out = [f"# {title}", "", f"Python snapshot `{commit}`. {total} sites. {intro}", "",
               "Written by `docs/rust-port/tools/site_lists.py`. A worker ticks the Done column "
               "when the Rust side reproduces the site and a vector or comparison session "
               "proves it.", "",
               "| Lane | Sites |", "| --- | ---: |"]
        out += [f"| {lane}. {LANES[lane][0]} | {len(lanes.get(lane, []))} |" for lane in LANES]
        for lane in LANES:
            rows = sorted(set(lanes.get(lane, [])))
            if not rows:
                continue
            out += ["", f"## Lane {lane}: {LANES[lane][0]}", "",
                    "| Where | Function | Kind | Code | Done |", "| --- | --- | --- | --- | --- |"]
            out += [f"| `{path[len(APP):]}:{line}` | `{where}` | {detail} | `{code}` | |"
                    for path, line, where, detail, code in rows]
        (OUT / filename).write_bytes(("\n".join(out) + "\n").encode("utf-8"))
        print(f"{filename}: {total} sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
