"""Export the tool batch's model-read texts as tool_batch_texts.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/provider_turn/generate_tool_batch_texts.py

Every text is read back from the Python source with `ast`, never retyped:

- `safe_error_reasons`: the `reasons` table of `_stateless_mcp._safe_error`, the reason a
  rejected tool call's result gives the model for each code.
- `instructions`: the `instruction` (or control `content`) texts of the tool batch's refusals
  and results in `_provider_turn_phases.py`. A text with a conditional part is split at it (the
  part's own key names its condition); an f-string is a template whose `{expression}` the Rust
  side fills with the same value.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AGENT = ROOT / "src" / "sylvia_gm" / "agent"
OUTPUT = Path(__file__).with_name("tool_batch_texts.json")


def function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


def assigned(node: ast.AST, target: str) -> ast.AST:
    """The value assigned to the local `target` inside `node`."""
    return next(statement.value for statement in ast.walk(node)
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                and any(isinstance(item, ast.Name) and item.id == target
                        for item in (statement.targets if isinstance(statement, ast.Assign)
                                     else [statement.target])))


def const(node: ast.AST) -> str:
    assert isinstance(node, ast.Constant) and isinstance(node.value, str), ast.dump(node)[:200]
    return node.value


def template(node: ast.AST) -> str:
    """An f-string with each replacement field written as `{expression}`."""
    assert isinstance(node, ast.JoinedStr), ast.dump(node)[:200]
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            assert "{" not in value.value and "}" not in value.value
            parts.append(value.value)
        else:
            assert isinstance(value, ast.FormattedValue) and value.format_spec is None
            assert value.conversion == -1
            parts.append("{" + ast.unparse(value.value) + "}")
    return "".join(parts)


def keyed(name: str, key: str) -> ast.AST:
    """The one value under `key` in a dict literal (or a `[key] =` assignment) in `name`."""
    found = []
    for node in ast.walk(function(AGENT / "_provider_turn_phases.py", name)):
        if isinstance(node, ast.Dict):
            found += [value for item, value in zip(node.keys, node.values)
                      if isinstance(item, ast.Constant) and item.value == key]
        elif (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
              and isinstance(node.targets[0].slice, ast.Constant)
              and node.targets[0].slice.value == key):
            found.append(node.value)
    assert len(found) == 1, (name, key, len(found))
    return found[0]


def held_suffix(node: ast.AST) -> str:
    """`(A if <held response> else "")`: A."""
    assert isinstance(node, ast.IfExp) and const(node.orelse) == "", ast.dump(node)[:200]
    return const(node.body)


def instructions() -> dict[str, str]:
    unavailable = keyed("reject_unavailable_tool", "instruction")
    assert isinstance(unavailable, ast.IfExp)
    revision_rejection = keyed("record_campaign_file_rejection", "instruction")
    assert isinstance(revision_rejection, ast.BinOp)
    unchanged = keyed("record_unchanged_campaign_file", "instruction")
    updated = keyed("record_updated_campaign_file", "instruction")
    assert isinstance(unchanged, ast.IfExp) and isinstance(updated, ast.IfExp)
    correction = keyed("retry_rejected_tool_call", "content")
    assert isinstance(correction, ast.BinOp) and isinstance(correction.left, ast.BinOp)
    return {
        "unavailable_tool_one_list": const(unavailable.body),
        "unavailable_tool_absent": const(unavailable.orelse),
        "selected_arguments_invalid": template(keyed("unwrap_selected_arguments", "instruction")),
        "tool_arguments_invalid_json": const(keyed("reject_invalid_tool_json", "instruction")),
        "missing_required_arguments": const(keyed("record_missing_tool_fields", "instruction")),
        "revised_narration_invalid": const(keyed("reject_invalid_revision", "instruction")),
        "campaign_file_rejection": const(revision_rejection.left),
        "campaign_file_rejection_held": held_suffix(revision_rejection.right),
        "duplicate_collections": const(keyed("restore_unchanged_narration", "instruction")),
        "unchanged_settled": const(unchanged.body),
        "unchanged_unsettled": const(unchanged.orelse),
        "updated_settled": const(updated.body),
        "updated_unsettled": const(updated.orelse),
        "tool_correction": const(correction.left.left),
        "tool_correction_held": held_suffix(correction.left.right),
        "tool_correction_count": template(correction.right),
    }


def safe_error_reasons() -> dict[str, str]:
    reasons = ast.literal_eval(assigned(function(AGENT / "_stateless_mcp.py", "_safe_error"),
                                        "reasons"))
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in reasons.items())
    return reasons


def main() -> None:
    texts = {"safe_error_reasons": safe_error_reasons(), "instructions": instructions()}
    OUTPUT.write_bytes((json.dumps(texts, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
