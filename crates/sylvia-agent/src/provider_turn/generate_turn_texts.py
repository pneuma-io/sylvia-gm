"""Export the turn's setup and loop controls as turn_texts.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/provider_turn/generate_turn_texts.py

Every text is read back from the Python source with `ast`, never retyped, through the helpers
of `generate_tool_batch_texts.py` (an f-string is a template whose `{expression}` the Rust
side fills with the same value):

- `redirected_continuation`, `post_mechanics_recovery`: the two controls
  `prepare_turn_recovery` adds, each before its JSON facts;
- `empty_completion`, `empty_required_tool`, `empty_response`: `retry_empty_completion`'s
  correction and the two forms of its `{required_output}`;
- `optional_reference_recovery`: the control `ProviderSessionAdapter._events` adds after an
  optional reference lookup failed.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_tool_batch_texts import AGENT, assigned, const, function, template  # noqa: E402

OUTPUT = Path(__file__).with_name("turn_texts.json")


def contents(node: ast.AST) -> list[ast.AST]:
    return [value for item in ast.walk(node) if isinstance(item, ast.Dict)
            for key, value in zip(item.keys, item.values)
            if isinstance(key, ast.Constant) and key.value == "content"]


def method(path: Path, owner: str, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == owner)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def main() -> None:
    recovery = contents(function(AGENT / "_provider_turn_phases.py", "prepare_turn_recovery"))
    assert len(recovery) == 2 and all(isinstance(node, ast.BinOp) for node in recovery)
    redirected, post_mechanics = (const(node.left) for node in recovery)
    assert redirected.startswith("SYLVIA_GM_REDIRECTED_CONTINUATION")
    assert post_mechanics.startswith("SYLVIA_GM_POST_MECHANICS_RECOVERY_CONTROL")
    empty = function(AGENT / "_provider_turn_phases.py", "retry_empty_completion")
    required_output = assigned(empty, "required_output")
    assert isinstance(required_output, ast.IfExp)
    (correction,) = contents(empty)
    (optional,) = [node for node in contents(method(AGENT / "_provider_session.py",
                                                    "ProviderSessionAdapter", "_events"))
                   if isinstance(node, ast.Constant)]
    texts = {
        "redirected_continuation": redirected,
        "post_mechanics_recovery": post_mechanics,
        "empty_completion": template(correction),
        "empty_required_tool": const(required_output.body),
        "empty_response": const(required_output.orelse),
        "optional_reference_recovery": const(optional),
    }
    OUTPUT.write_bytes((json.dumps(texts, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
