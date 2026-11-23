"""Export the action-sequence queue's model tools as action_sequence_tools.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/generate_action_sequence_tools.py

`_stateless_mcp.action_sequence_model_tools(owner)` advertises four application tools whose
descriptions and schemas the model reads; they are read back from the Python function, never
retyped. Its owner check passes for every real `AuthorityOwner`, which has all four services.
Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent._stateless_mcp import action_sequence_model_tools  # noqa: E402

OUTPUT = Path(__file__).with_name("action_sequence_tools.json")


def main() -> None:
    owner = SimpleNamespace(**{name: (lambda *_args, **_kwargs: None) for name in (
        "action_sequence", "campaign_panels", "_compose_action_sequence_from_agent",
        "_execute_action_sequence_from_agent")})
    tools = action_sequence_model_tools(owner)  # type: ignore[arg-type]
    assert len(tools) == 4
    exported = [{
        "package_id": tool.package_id, "package_version": tool.package_version,
        "package_sha256": tool.package_sha256, "name": tool.name,
        "capability": tool.capability, "input_schema": tool.input_schema,
        "parameter_derivations": tool.parameter_derivations, "description": tool.description,
        "category": tool.category, "model_behavior": tool.model_behavior.value,
    } for tool in tools]
    text = json.dumps(exported, ensure_ascii=True, indent=1, allow_nan=False) + chr(10)
    assert json.loads(text) == exported
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(exported), "tools,", len(text), "bytes")


if __name__ == "__main__":
    main()
