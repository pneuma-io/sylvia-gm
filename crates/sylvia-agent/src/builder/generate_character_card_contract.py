"""Export get_character_card_import_contract's answer as character_card_contract.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/builder/generate_character_card_contract.py

The answer is read back from Python's own tool handler (`character_profile_tools`), so the
constants of `application/character_card_import.py` it lists are never retyped. Two runs give
identical bytes. Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent._character_profile_tools import character_profile_tools  # noqa: E402

OUTPUT = Path(__file__).with_name("character_card_contract.json")
NL = chr(10)


def unused(*_args, **_kwargs):
    raise AssertionError("the contract reads no library")


def main() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        tools = character_profile_tools(Path(workspace), save=unused, read=unused)
        contract_tool = next(tool for tool in tools if tool.name == "get_character_card_import_contract")
        contract = contract_tool.handler({}, None)
    output = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "contract": contract}
    OUTPUT.write_text(json.dumps(output, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, len(contract))


if __name__ == "__main__":
    main()
