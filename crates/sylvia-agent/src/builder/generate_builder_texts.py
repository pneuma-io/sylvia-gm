"""Export the Builder's model-read texts as builder_texts.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/builder/generate_builder_texts.py

The texts are read back from the Python source, never retyped:

- `prompt`: every string of `agent._builder_session._builder_prompt`, read from its syntax tree.
  Each conditional instruction is the string its `if` branch yields (the `else` branch is
  always the empty string). The returned f-string and the two f-strings it uses are lists of
  parts, where a text part is a string and a substituted value is `{"name": <expression>}`;
- `title_instruction`: `agent._builder_title.TITLE_INSTRUCTION`;
- `skills`: each tracked skill's `SKILL.md` and its packaged references, in
  `builder_skill_references` order, read through `agent._skills`.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _builder_session as session  # noqa: E402
from sylvia_gm.agent import _builder_title as title  # noqa: E402
from sylvia_gm.agent import _skills as skills  # noqa: E402

OUTPUT = Path(__file__).with_name("builder_texts.json")
NL = chr(10)


def parts(node: ast.expr) -> list:
    """A string or an f-string as text parts and substituted names."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        result = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                result.append(value.value)
            elif isinstance(value, ast.FormattedValue) and value.conversion == -1 and value.format_spec is None:
                result.append({"name": ast.unparse(value.value)})
            else:
                raise ValueError(f"unexpected f-string part {ast.dump(value)}")
        return result
    raise ValueError(f"unexpected string node {ast.dump(node)}")


def text(node: ast.expr) -> str:
    """A plain string (an implicitly joined literal), never an f-string."""
    pieces = parts(node)
    if not all(isinstance(piece, str) for piece in pieces):
        raise ValueError("expected a plain string")
    return "".join(pieces)


def prompt_texts() -> dict:
    source = Path(session.__file__).read_text(encoding="utf-8")
    function = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_builder_prompt"
    )
    result: dict = {}
    for node in function.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.endswith("_instruction"):
                branch = node.value
                if not isinstance(branch, ast.IfExp):
                    raise ValueError(name)
                if name == "skill_instruction":
                    # The general text is the branch taken when no skill is tracked.
                    result["skill_general"] = text(branch.body)
                    result["skill_selected"] = parts(branch.orelse)
                else:
                    if text(branch.orelse) != "":
                        raise ValueError(f"{name}: the other branch is not empty")
                    result[name.removesuffix("_instruction")] = text(branch.body)
            elif name == "skill_blocks":
                continue
        elif isinstance(node, ast.For):
            append = node.body[-1].value
            result["skill_block"] = parts(append.args[0])
        elif isinstance(node, ast.Return):
            if not (isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add)):
                raise ValueError("unexpected return")
            result["template"] = parts(node.value.left)
            join = node.value.right
            if not (isinstance(join, ast.Call) and isinstance(join.func, ast.Attribute)
                    and join.func.attr == "join" and ast.unparse(join.args[0]) == "skill_blocks"):
                raise ValueError("unexpected join")
            result["block_separator"] = text(join.func.value)
    expected = {"skill_general", "skill_selected", "subflow", "launch", "participant",
                "required_character", "repair", "skill_block", "template", "block_separator"}
    if set(result) != expected:
        raise ValueError(f"prompt texts {sorted(result)}")
    return result


def skill_texts() -> dict:
    result = {}
    for name in skills.builder_skills():
        references = skills.builder_skill_references(name)
        result[name] = {
            "directory": skills._SKILL_DIRECTORIES[name],
            "text": skills.load_builder_skill(name).text,
            "references": [[path, skills.load_builder_skill(name, reference=path).text]
                           for path in references],
        }
    return result


def main() -> None:
    texts = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "prompt": prompt_texts(),
        "title_instruction": title.TITLE_INSTRUCTION,
        "skills": skill_texts(),
    }
    OUTPUT.write_text(json.dumps(texts, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, OUTPUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
