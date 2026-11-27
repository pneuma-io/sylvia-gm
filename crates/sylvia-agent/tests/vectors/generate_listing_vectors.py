"""Write listing_vectors.json: the MCP tool listing (T3m-1), traced in real turns.

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_listing_vectors.py

It records `ruleless-turn` and `srd-mechanics-turn` through `generate_round_vectors.record`
with the `list_tools` pseudo-phase traced (`trace_turn_phases.py`). Each case holds one call of
`StatelessMcpMechanics._list_tools`: its arguments, every owner call it made with the answer
(the Full Audit record among them; a catalog tool keeps the fields the listing reads), and
the tools it returned or what it raised. The first
listing with a session in each run is repeated with `listing_variants`.

Large repeated values are interned as in `generate_batch_vectors.py`. Reference tooling only;
retired with the Python source.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_batch_vectors import interned  # noqa: E402
from generate_round_vectors import DURATION, ROOT, TIME, record  # noqa: E402

OUTPUT = HERE / "listing_vectors.json"
SESSIONS = ("ruleless-turn", "srd-mechanics-turn")
#: The `CatalogTool` fields the listing reads; the rest (its callables' reprs among them) vary
#: from run to run.
CATALOG_FIELDS = ("package_id", "package_version", "package_sha256", "name", "capability",
                  "input_schema", "description", "model_behavior")


#: `list_tools()` draws its own discovery identity from an unseeded `uuid4()` (the call tool
#: revalidation's); each one found is renamed in order of first appearance.
DISCOVERY = re.compile(r"mcp-discovery-[0-9a-f]{32}")


def renamed_discoveries(text: str) -> str:
    names: dict[str, str] = {}
    return DISCOVERY.sub(
        lambda match: names.setdefault(match.group(0), f"mcp-discovery-{len(names) + 1:032x}"), text)


def slim(call: dict[str, object]) -> dict[str, object]:
    if call["name"] == "catalog" and isinstance(call.get("result"), list):
        call = {**call, "result": [{key: tool[key] for key in CATALOG_FIELDS}
                                   for tool in call["result"]]}  # type: ignore[index, union-attr]
    return call


def cases(rows: list[dict[str, object]], session: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for row in rows:
        if row["event"] == "listing_start":
            current = {"session": session, **{key: value for key, value in row.items()
                                               if key != "event"}, "calls": []}
        elif row["event"] == "listing_end" and current is not None:
            current.update({key: value for key, value in row.items() if key != "event"})
            found.append(current)
            current = None
        elif current is not None and row["event"] == "call":
            current["calls"].append(slim(row))  # type: ignore[union-attr]
    return found


def main() -> None:
    found = [case for name in SESSIONS
             for case in cases(record("list_tools", ROOT / f"crates/sylvia-compare/sessions/{name}.session.json"), name)]
    text = json.dumps(found, ensure_ascii=False, allow_nan=False)
    assert " at 0x" not in text, "a recorded value carries an object address"
    text = renamed_discoveries(text)
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    text = DURATION.sub(lambda match: f'"{match.group(1)}": 0', text)
    found = json.loads(text)
    shared: dict[str, object] = {}
    written = [interned(case, shared) for case in found]
    text = json.dumps({"shared": shared, "cases": written}, ensure_ascii=False, indent=1,
                      allow_nan=False) + "\n"
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(found), "cases,", len(text), "bytes")
    for case in found:
        outcome = case.get("raised", {}).get("code") if "raised" in case else len(case["tools"])
        print(" ", case["session"], case["purpose"], case["variant"], "|", outcome, "|",
              [call["name"] for call in case["calls"]])


if __name__ == "__main__":
    main()
