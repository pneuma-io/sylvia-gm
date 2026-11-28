"""Export the narration-only Retry's controls as retry_texts.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/provider_session/generate_retry_texts.py

The texts are model-read, so they are read back from `ProviderSessionAdapter.for_retry`'s source
with `ast`, never retyped:

- `retry_control_prefix`, `retry_control_opening`, `retry_control_mechanic` and
  `retry_control_suffix`: the `SYLVIA_GM_RETRY_CONTROL` message, its opening sentence or its
  committed-mechanic sentence between the prefix and the suffix;
- `retry_data_prefix`: the `SYLVIA_GM_CANONICAL_RETRY_DATA` message before its JSON.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("retry_texts.json")


def method(path: Path, owner: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == owner)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def flat(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return flat(node.left) + flat(node.right)
    return [node]


def main() -> None:
    function = method(ROOT / "src/sylvia_gm/agent/_provider_session.py", "ProviderSessionAdapter", "for_retry")
    contents = [value for node in ast.walk(function) if isinstance(node, ast.Dict)
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and key.value == "content"]
    assert len(contents) == 2, len(contents)
    control, data = (flat(node) for node in contents)
    prefix, choice, suffix = control
    assert isinstance(choice, ast.IfExp)
    opening, mechanic = (ast.literal_eval(flat(part)[0]) if len(flat(part)) == 1 else
                         "".join(ast.literal_eval(item) for item in flat(part))
                         for part in (choice.body, choice.orelse))
    data_prefix, dumped = data
    assert isinstance(dumped, ast.Call)
    texts = {
        "retry_control_prefix": ast.literal_eval(prefix),
        "retry_control_opening": opening,
        "retry_control_mechanic": mechanic,
        "retry_control_suffix": ast.literal_eval(suffix),
        "retry_data_prefix": ast.literal_eval(data_prefix),
    }
    assert texts["retry_control_prefix"].startswith("SYLVIA_GM_RETRY_CONTROL ")
    assert texts["retry_data_prefix"].startswith("SYLVIA_GM_CANONICAL_RETRY_DATA ")
    OUTPUT.write_bytes((json.dumps(texts, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
