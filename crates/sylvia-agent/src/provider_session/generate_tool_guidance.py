"""Export the provider tool projection's long model-facing values as tool_guidance.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/provider_session/generate_tool_guidance.py

The values are model-read, so their text is copied from the Python values, never retyped:

- `preparation_intent_argument`: the property `_model_tool_schema` adds to a tool whose
  behaviour leaves the player's intent pending;
- `campaign_file_revised_narration_argument`: the property it adds to the Campaign File update;
- `arguments_tool`: `agent/_argument_contracts.py`'s `ARGUMENTS_TOOL`.

The first two are read back from `_model_tool_schema` on probe tools with no other properties,
so they are exactly what the function inserts. Each object's keys keep Python's order. This
script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _argument_contracts, _provider_session  # noqa: E402
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool  # noqa: E402
from sylvia_gm.domain.authority import ModelToolBehavior  # noqa: E402

OUTPUT = Path(__file__).with_name("tool_guidance.json")


def probe(**fields: object) -> dict[str, object]:
    tool = ModelMechanicsTool(
        package_id="probe", package_version="1", package_sha256="0" * 64, name="probe",
        capability=str(fields.pop("capability", "probe")),
        input_schema={"type": "object", "properties": {}}, **fields,  # type: ignore[arg-type]
    )
    properties = _provider_session._model_tool_schema(tool)["properties"]
    assert isinstance(properties, dict) and len(properties) == 1, properties
    return next(iter(properties.values()))


def main() -> None:
    exported = {
        "preparation_intent_argument": probe(
            model_behavior=ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING),
        "campaign_file_revised_narration_argument": probe(
            category="narrative_state", capability="narrative_state.update_campaign_file"),
        "arguments_tool": _argument_contracts.ARGUMENTS_TOOL,
    }
    text = json.dumps(exported, ensure_ascii=True, indent=1, allow_nan=False) + chr(10)
    assert json.loads(text) == exported, "a value is not plain JSON"
    OUTPUT.write_text(text, encoding="utf-8", newline=chr(10))
    print(len(exported), "values:", ", ".join(exported), len(text), "bytes")


if __name__ == "__main__":
    main()
