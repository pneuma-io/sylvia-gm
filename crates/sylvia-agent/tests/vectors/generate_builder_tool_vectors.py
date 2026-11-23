"""Generate reference vectors for the Builder tool layer (B2a), one file per platform.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_builder_tool_vectors.py

It writes `builder_tool_vectors.windows.json` or `builder_tool_vectors.linux.json`, because the
results hold `str(Path)` text and directory listings in the platform's order.

A real `AgentRunner` (BUILDER_FREEFORM, public web allowed) over a scripted workspace tree. Records:
- `tree`: the workspace as created, in creation order (`[path, text]`, `[path, {"hex": ...}]` for
  bytes, `[path, null]` for a directory);
- `definitions`: the registry's definitions for each mode, with and without public web;
- `steps`: one `registry.execute(name, params)` after another, each result with the workspace
  root written `<ROOT>`; later steps see the files earlier steps wrote;
- `registry`: execution shapes over a fresh registry (unknown tool, a handler raising, a non-dict
  result, a result without `success`) and `tool_spec_to_openai` of parameter-only specs;
- `after`: every file left in the workspace with its SHA-256.
Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent._mode import AgentMode  # noqa: E402
from sylvia_gm.agent._runner import AgentRunner  # noqa: E402
from sylvia_gm.runtime._types import ToolParameter  # noqa: E402
from sylvia_gm.tools._converter import tool_spec_to_openai  # noqa: E402
from sylvia_gm.tools._registry import ToolRegistry  # noqa: E402
from sylvia_gm.tools._spec import ToolSpec  # noqa: E402
from sylvia_gm.tools._toolsets import ToolsetName  # noqa: E402

PLATFORM = "windows" if os.name == "nt" else "linux"
OUTPUT = HERE / f"builder_tool_vectors.{PLATFORM}.json"
NL = chr(10)
CR = chr(13)
BS = chr(92)

TREE = [
    ["notes.md", "# Notes" + NL + "First line" + NL + "Second line with TODO" + NL],
    ["crlf.txt", "one" + CR + NL + "two TODO" + CR + NL + "three"],
    ["bom.md", chr(0xFEFF) + "BOM first TODO" + NL],
    ["unicode.md", "Ünïcödé TODO wörds" + NL + "日本語 TODO" + NL + "x" * 250 + " TODO" + NL],
    ["UPPER.MD", "upper case TODO" + NL],
    ["marks.md", "e" + chr(0x301) + "x mark" + NL],
    ["binary.bin", {"hex": "00010203ff"}],
    ["latin.txt", {"hex": "e9e8"}],
    ["b", None],
    ["b/inside.md", "b inside TODO" + NL],
    ["b/c", None],
    ["b/c/leaf.md", "b c leaf TODO" + NL],
    ["a", None],
    ["a/z", None],
    ["a/y", None],
    ["a/top.md", "a top TODO" + NL],
    ["a/z/deep.md", "z deep TODO" + NL],
    ["a/y/deep.md", "y deep TODO" + NL],
    ["a/y/data.json", '{"todo": true}' + NL],
    ["a/y/w", None],
    ["a/y/w/leaf.md", "a y w leaf TODO" + NL],
    ["many", None],
    *[[f"many/file{index:02d}.txt", (f"TODO {index} line" + NL) * 3] for index in range(40)],
    [".builder-sources", None],
    [".builder-sources/attached.md", "attached TODO" + NL],
    [".builder-sources-purge-pending", None],
    [".builder-sources-purge-pending/hidden.md", "hidden TODO" + NL],
    ["empty.txt", ""],
    ["long.txt", "".join(f"line {index}" + NL for index in range(1, 2601))],
    ["huge.txt", ("y" * 999 + NL) * 120],
    ["afile.md", "a file" + NL],
    ["dir.md", None],
    ["two.md", "same same" + NL],
    [".env.local", "SECRET=1" + NL],
]


def make_tree(root: Path) -> None:
    for path, content in TREE:
        target = root / path
        if content is None:
            target.mkdir()
        elif isinstance(content, dict):
            target.write_bytes(bytes.fromhex(content["hex"]))
        else:
            target.write_bytes(content.encode("utf-8"))


def mask(value, roots):
    text = json.dumps(value, ensure_ascii=False)
    for root in roots:
        for form in (root.replace(BS, BS * 4), root.replace(BS, BS * 2), root.replace(BS, "/")):
            text = text.replace(form, "<ROOT>")
    return json.loads(text)


def steps(version_of):
    """The scripted calls; `version_of(path)` reads a version an earlier step recorded."""
    yield "read_file", {"path": "notes.md"}
    yield "read_file", {"path": "notes.md", "offset": 2, "limit": 1}
    yield "read_file", {"path": "notes.md", "offset": 0, "limit": 2}
    yield "read_file", {"path": "notes.md", "offset": -5}
    yield "read_file", {"path": "notes.md", "offset": 99}
    yield "read_file", {"path": "notes.md", "limit": -1}
    yield "read_file", {"path": "notes.md", "limit": 0}
    yield "read_file", {"path": "notes.md", "offset": True, "limit": True}
    yield "read_file", {"path": "notes.md", "offset": 2.0}
    yield "read_file", {"path": "notes.md", "limit": 2.5}
    yield "read_file", {"path": "notes.md", "offset": "2"}
    yield "read_file", {"path": "notes.md", "limit": "2"}
    yield "read_file", {"path": "notes.md", "offset": None}
    yield "read_file", {"path": "notes.md", "limit": [1]}
    yield "read_file", {"path": "notes.md", "limit": 5000}
    yield "read_file", {"path": "long.txt", "offset": 2550}
    yield "read_file", {"path": "huge.txt", "limit": 2000}
    yield "read_file", {"path": "crlf.txt"}
    yield "read_file", {"path": "bom.md"}
    yield "read_file", {"path": "unicode.md"}
    yield "read_file", {"path": "upper.md"}
    yield "read_file", {"path": "binary.bin"}
    yield "read_file", {"path": "latin.txt"}
    yield "read_file", {"path": "empty.txt"}
    yield "read_file", {"path": "missing.md"}
    yield "read_file", {"path": "dir.md"}
    yield "read_file", {"path": "a/./y/../top.md"}
    yield "read_file", {"path": "../outside.md"}
    yield "read_file", {"path": ".builder-sources/attached.md"}
    yield "read_file", {"path": ".builder-sources-purge-pending/hidden.md"}
    yield "read_file", {"path": ""}
    yield "read_file", {}
    yield "read_file", {"path": 5}
    yield "read_file", {"path": ["notes.md"]}
    yield "write_file", {"path": "new/deep/file.md", "content": "Fresh" + NL}
    yield "write_file", {"path": "new/deep/file.md", "content": "Again"}
    yield "write_file", {"path": "new/deep/file.md", "content": "Again", "expected_version": version_of("new/deep/file.md")}
    yield "write_file", {"path": "new/deep/file.md", "content": "Stale", "expected_version": "0" * 64}
    yield "write_file", {"path": "fresh.md", "content": "x", "expected_version": ""}
    yield "write_file", {"path": "fresh2.md", "content": "x", "expected_version": 5}
    yield "write_file", {"path": "fresh3.md", "content": "x", "expected_version": False}
    yield "write_file", {"path": "nothing.md"}
    yield "write_file", {"path": "nothing.md", "content": None}
    yield "write_file", {"path": "nothing.md", "content": ""}
    yield "write_file", {"path": "nothing.md", "content": 5}
    yield "write_file", {"path": "dir.md", "content": "x"}
    yield "write_file", {"path": "afile.md/under.md", "content": "x"}
    yield "write_file", {"path": ".env.local", "content": "x"}
    yield "write_file", {"path": "keys/server.pem", "content": "x"}
    yield "write_file", {"path": ".builder-sources/attached.md", "content": "x"}
    yield "write_file", {"path": "../escape.md", "content": "x"}
    yield "read_file", {"path": "two.md"}
    yield "edit_file", {"path": "two.md", "old_text": "same", "new_text": "one", "expected_version": version_of("two.md")}
    yield "edit_file", {"path": "two.md", "old_text": "same same", "new_text": "one", "expected_version": version_of("two.md")}
    yield "read_file", {"path": "afile.md/inner.md"}
    yield "edit_file", {"path": "notes.md", "old_text": "absent", "new_text": "x", "expected_version": version_of("notes.md")}
    yield "edit_file", {"path": "notes.md", "old_text": "Second", "new_text": "2nd", "expected_version": version_of("notes.md")}
    yield "edit_file", {"path": "notes.md", "old_text": "First", "new_text": "1st", "expected_version": version_of("notes.md")}
    yield "edit_file", {"path": "notes.md", "old_text": "", "new_text": "x", "expected_version": "v"}
    yield "edit_file", {"path": "notes.md", "old_text": 5, "new_text": "x", "expected_version": "v"}
    yield "edit_file", {"path": "notes.md", "old_text": "x", "new_text": 5, "expected_version": "v"}
    yield "edit_file", {"path": "notes.md", "old_text": "x", "new_text": "y"}
    yield "edit_file", {"path": "notes.md", "old_text": "x", "new_text": "y", "expected_version": ""}
    yield "edit_file", {"path": "missing.md", "old_text": "x", "new_text": "y", "expected_version": "missing"}
    yield "edit_file", {"path": "missing.md", "old_text": "x", "new_text": "y", "expected_version": "0" * 64}
    yield "read_file", {"path": "notes.md"}
    yield "delete_file", {"path": "fresh.md", "expected_version": version_of("fresh.md")}
    yield "delete_file", {"path": "fresh.md"}
    yield "delete_file", {"path": "fresh3.md"}
    yield "delete_file", {"path": "dir.md"}
    yield "delete_file", {"path": "afile.md", "expected_version": "0" * 64}
    yield "delete_file", {"path": ".builder-sources/attached.md"}
    yield "delete_file", {}
    for pattern in ("*.md", "*", "**/*.md", "*.MD", "a/*", "a/**/*.md", "a/**/y/**/*.json", "?.md", "[ab]*",
                    "[!a]*.md", "[a-c]/*", "[z-a]*", "[!]*", "many/file0[0-4].txt", "many/file[!0-3]?.txt",
                    "**", "", "a/", "..", "../*", "nothing*", "a**b", "/abs", "C:/x", "a/./top.md",
                    "a//top.md", "*.[mj][ds]*", "[[]*", "*[", "a-b", "[a-]*", "[-a]*", "[a--z]*", "[a-b-c]*"):
        yield "search_files", {"pattern": pattern}
    yield "search_files", {"pattern": "*.md", "path": "a"}
    yield "search_files", {"pattern": "*.md", "path": "notes.md"}
    yield "search_files", {"pattern": "*.md", "path": "missing"}
    yield "search_files", {"pattern": "*.md", "path": "../"}
    yield "search_files", {}
    yield "search_files", {"pattern": None}
    yield "search_files", {"pattern": 0}
    yield "search_files", {"pattern": 5}
    yield "search_files", {"pattern": ["x"]}
    for pattern in ("TODO", r"^\w+ \d+", r"\bdeep\b", "(?i)todo", r"[A-Z]\w*", r"(?P<w>e)(?P=w)", r"line$",
                    r"\.json", "a{,2}", "x{", "日本", r"\s{2,}", r"[^\x00-\x7f]", r"TODO \d{1,2}(?= line)",
                    r"(?<=file)\d", "(?i:Ö)", "Ü|wörds", r"\Atwo", r"three\Z", r"(a)\1", "zzz-none", "y" * 5, "leaf", "e" + chr(92) + "w"):
        yield "search_files", {"pattern": pattern, "target": "content"}
    yield "search_files", {"pattern": "TODO", "target": "content", "path": "a"}
    yield "search_files", {"pattern": "TODO", "target": "content", "path": "unicode.md"}
    yield "search_files", {"pattern": "TODO", "target": "lines"}
    # One untranslatable pattern and one bad pattern (differences row).
    yield "search_files", {"pattern": "(?x) T O D O", "target": "content"}
    yield "search_files", {"pattern": "(", "target": "content"}
    yield "search_files", {"pattern": "(?<=T+)O", "target": "content"}
    yield "search_files", {"pattern": "*a", "target": "content"}
    yield "search_files", {"pattern": "a**", "target": "content"}
    yield "search_files", {"pattern": "x{2,1}", "target": "content"}
    yield "search_files", {"pattern": "[a", "target": "content"}
    yield "search_files", {"pattern": "\\", "target": "content"}
    yield "search_files", {"pattern": r"\q", "target": "content"}
    yield "search_files", {"pattern": r"\9", "target": "content"}
    yield "search_files", {"pattern": "a(?i)b", "target": "content"}
    yield "search_files", {"pattern": None, "target": "content"}
    for params in ({"question": "Pick?", "options": ["A", "B"], "id": "q1"},
                   {"type": "text", "question": "Name?", "id": "q2"},
                   {"type": "confirm", "question": "Sure?"},
                   {"type": "confirm", "question": "Sure?", "options": []},
                   {"type": "select", "question": ""},
                   {"type": "cascade", "question": "Choose", "options": None, "action_label": "Go"},
                   {"type": "cascade"},
                   {"type": "complete", "summary": "Done", "campaign_id": "c1", "id": "z"},
                   {"type": "plan_complete"},
                   {"type": 5, "question": "Odd?", "id": 7},
                   {}):
        yield "ask_question", params
    for params in ({}, {"query": "  "}, {"query": "dice", "limit": 0}, {"query": "dice", "limit": 11},
                   {"query": "dice", "limit": True}, {"query": "dice", "limit": 2.0}):
        yield "web_search", params
    yield "unknown_tool", {"x": 1}


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        workspace = Path(scratch) / "workspace"
        workspace.mkdir()
        make_tree(workspace)
        roots = [str(workspace.resolve()), str(workspace), str(Path(scratch).resolve()), scratch]
        runner = AgentRunner(mode=AgentMode.BUILDER_FREEFORM, provider=None, history_path=None,
                             initial_history=[], project_root=workspace, allow_public_web=True)
        quiet = AgentRunner(mode=AgentMode.BUILDER_FREEFORM, provider=None, history_path=None,
                            initial_history=[], project_root=workspace)
        definitions = {
            "web": runner._registry.get_definitions(AgentMode.BUILDER_FREEFORM),
            "no_web": quiet._registry.get_definitions(AgentMode.BUILDER_FREEFORM),
            "interview": runner._registry.get_definitions(AgentMode.BUILDER_INTERVIEW),
            "dm": runner._registry.get_definitions(AgentMode.DM_SESSION),
        }
        versions: dict[str, str] = {}

        def version_of(path: str) -> str:
            return versions.get(path, "")

        recorded = []
        for name, params in steps(version_of):
            result = runner._registry.execute(name, dict(params), AgentMode.BUILDER_FREEFORM)
            if isinstance(result.get("version"), str) and isinstance(params.get("path"), str):
                versions[params["path"]] = result["version"]
            recorded.append({"tool": name, "params": params, "result": mask(result, roots)})
        after = sorted(
            [path.relative_to(workspace).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
            for path in workspace.rglob("*") if path.is_file()
        )
    registry = ToolRegistry()

    def raising(_params, _context):
        raise ValueError("boom")

    schema = {"type": "function", "function": {"name": "shape", "parameters": {"type": "object"}}}
    for name, handler in (("raises", raising), ("text", lambda p, c: "x"), ("bare", lambda p, c: {"a": 1}),
                          ("failed", lambda p, c: {"error": "e", "success": False})):
        registry.register(ToolSpec.from_openai_schema(
            {"type": "function", "function": {**schema["function"], "name": name}},
            handler=handler, toolsets=frozenset({ToolsetName.BUILDER_TOOLS})))
    executions = [[name, registry.execute(name, {}, AgentMode.BUILDER_FREEFORM)]
                  for name in ("raises", "text", "bare", "failed", "absent")]
    plain = ToolSpec(name="plain", description="Plain tool", handler=raising,
                     toolsets=frozenset({ToolsetName.BUILDER_TOOLS}), parameters=[
                         ToolParameter(name="path", type="string", description="A path"),
                         ToolParameter(name="tags", type="array", required=False),
                         ToolParameter(name="mode", type="string", enum_values=["a", "b"], default="a"),
                         ToolParameter(name="count", type="integer", required=True, default=3)])
    bare = {"type": "function", "function": {"name": "bare"}}
    typed = {"type": "function", "function": {"name": "typed", "description": "T", "parameters": {
        "type": "object", "properties": {"n": {"type": "number"}, "o": {}, "x": {"type": "weird"}}, "required": ["n"]}}}
    conversions = [tool_spec_to_openai(plain),
                   tool_spec_to_openai(ToolSpec.from_openai_schema(bare, handler=raising, toolsets=frozenset())),
                   tool_spec_to_openai(ToolSpec.from_openai_schema(typed, handler=raising, toolsets=frozenset()))]
    parsed = [[p.name, p.type, p.required, p.default, p.enum_values, p.description]
              for p in ToolSpec.from_openai_schema(typed, handler=raising, toolsets=frozenset()).parameters]
    vectors = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "platform": PLATFORM,
        "tree": TREE,
        "definitions": definitions,
        "steps": recorded,
        "registry": {"executions": executions, "conversions": conversions, "parsed": parsed},
        "after": after,
    }
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    print(OUTPUT.name, OUTPUT.stat().st_size, "bytes,", len(recorded), "steps")


if __name__ == "__main__":
    main()
