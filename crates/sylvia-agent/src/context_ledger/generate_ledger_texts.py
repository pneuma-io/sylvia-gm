"""Export the context ledger's fixed model-read texts as ledger_texts.json, value for value.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/context_ledger/generate_ledger_texts.py

The texts are read back from the Python functions and constants that produce them, never
retyped:

- `ledger_contract_prefix` and `ledger_contract_suffix`: `ledger_contract(seal)` before and
  after its one seal token (`[host-seal <seal>]`);
- `withdrawn_suffix`: `_withdrawn(label)` after its label;
- `facts_header`, `facts_added_header` and `update_header`: `_FACTS_HEADER`,
  `_FACTS_ADDED_HEADER` and `_UPDATE_HEADER`;
- `continue_text`: `_CONTINUE_TEXT`, which `sylvia_provider::context_mode::CONTINUE_RECORD_TEXT`
  must equal.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _context_ledger as ledger  # noqa: E402

SEAL = "0123456789ab"


def split_once(text: str, marker: str) -> tuple[str, str]:
    assert text.count(marker) == 1, marker
    before, after = text.split(marker)
    return before, after


def main() -> None:
    token = ledger.seal_prefix(SEAL).strip()
    prefix, suffix = split_once(ledger.ledger_contract(SEAL), token)
    label = "\x00LABEL\x00"
    withdrawn = ledger._withdrawn(label)
    assert withdrawn.startswith(label) and withdrawn.count(label) == 1
    texts = {
        "ledger_contract_prefix": prefix,
        "ledger_contract_suffix": suffix,
        "withdrawn_suffix": withdrawn[len(label):],
        "facts_header": ledger._FACTS_HEADER,
        "facts_added_header": ledger._FACTS_ADDED_HEADER,
        "update_header": ledger._UPDATE_HEADER,
        "continue_text": ledger._CONTINUE_TEXT,
    }
    out = Path(__file__).with_name("ledger_texts.json")
    out.write_bytes((json.dumps(texts, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
