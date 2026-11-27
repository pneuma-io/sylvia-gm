"""Export the narration phases' model-read controls as narration_texts.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/provider_turn/generate_narration_texts.py

Every text is read back from `_provider_turn_phases.py` with `ast`, never retyped, through the
helpers of `generate_tool_batch_texts.py`: an f-string is a template whose `{expression}` the
Rust side fills with the same value.

- `repeated_response`, `required_tool_call`, `private_deliberation`: the corrections
  `retry_repeated_response`, `retry_missing_tool_call` and `retry_private_deliberation` add;
- `campaign_file_held`, `campaign_file_rejected`, `campaign_file_count`:
  `retry_missing_campaign_file_update`'s control, its two openings (a held response, or a
  rejected call) and the correction count that follows either;
- `ruleset_decision_active`, `player_intent_pending`: the controls `preserve_boundary_narration`
  and `preserve_player_intent_narration` add.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_tool_batch_texts import AGENT, const, function, keyed, template  # noqa: E402

OUTPUT = Path(__file__).with_name("narration_texts.json")


def control(name: str) -> ast.AST:
    """The one literal `content` (a string or an f-string) in `name`: its control. A message
    whose content is the narration itself is not one."""
    found = [value for node in ast.walk(function(AGENT / "_provider_turn_phases.py", name))
             if isinstance(node, ast.Dict)
             for item, value in zip(node.keys, node.values)
             if isinstance(item, ast.Constant) and item.value == "content"
             and isinstance(value, (ast.Constant, ast.JoinedStr))]
    assert len(found) == 1, (name, len(found))
    return found[0]


def main() -> None:
    campaign_file = keyed("retry_missing_campaign_file_update", "content")
    assert isinstance(campaign_file, ast.BinOp) and isinstance(campaign_file.left, ast.IfExp)
    texts = {
        "repeated_response": template(control("retry_repeated_response")),
        "required_tool_call": template(control("retry_missing_tool_call")),
        "private_deliberation": template(control("retry_private_deliberation")),
        "campaign_file_held": const(campaign_file.left.body),
        "campaign_file_rejected": const(campaign_file.left.orelse),
        "campaign_file_count": template(campaign_file.right),
        "ruleset_decision_active": const(control("preserve_boundary_narration")),
        "player_intent_pending": const(control("preserve_player_intent_narration")),
    }
    OUTPUT.write_bytes((json.dumps(texts, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
