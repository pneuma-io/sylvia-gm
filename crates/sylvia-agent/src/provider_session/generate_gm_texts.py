"""Export the GM request's long fixed texts as gm_texts.json, value for value.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/provider_session/generate_gm_texts.py

The texts are model-read, so they are read back from the Python functions and constants that
produce them, never retyped:

- `gm_system_prompt`: `_dm_system_prompt(...)`, the saved GM system contract (its arguments
  are unused);
- `gm_system_contract_version`: `_GM_SYSTEM_CONTRACT_VERSION`;
- `gm_context_privacy`: `_GM_CONTEXT_PRIVACY`;
- `gm_model_narration_block`: `_GM_MODEL_NARRATION_BLOCK`;
- `response_format_contract`: `_response_format_contract_message()`'s content;
- `opening_task_prefix`: `_campaign_opening_task_message`'s content before the footing JSON.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _provider_session as session  # noqa: E402

OUTPUT = Path(__file__).with_name("gm_texts.json")


def main() -> None:
    prompts = {(mechanics, tools): session._dm_system_prompt(  # noqa: SLF001
        campaign_has_mechanics=mechanics, tools_available=tools)
        for mechanics in (False, True) for tools in (False, True)}
    assert len(set(prompts.values())) == 1, "the GM system prompt depends on its arguments"
    opening = session._campaign_opening_task_message({})["content"]  # noqa: SLF001
    assert isinstance(opening, str) and opening.endswith("{}")
    exported = {
        "gm_system_prompt": prompts[(False, False)],
        "gm_system_contract_version": session._GM_SYSTEM_CONTRACT_VERSION,  # noqa: SLF001
        "gm_context_privacy": session._GM_CONTEXT_PRIVACY,  # noqa: SLF001
        "gm_model_narration_block": session._GM_MODEL_NARRATION_BLOCK,  # noqa: SLF001
        "response_format_contract":
            session._response_format_contract_message()["content"],  # noqa: SLF001
        "opening_task_prefix": opening[: -len("{}")],
    }
    text = json.dumps(exported, ensure_ascii=True, indent=1, allow_nan=False) + chr(10)
    assert json.loads(text) == exported
    OUTPUT.write_text(text, encoding="utf-8", newline=chr(10))
    print(len(exported), "values,", len(text), "bytes")


if __name__ == "__main__":
    main()
