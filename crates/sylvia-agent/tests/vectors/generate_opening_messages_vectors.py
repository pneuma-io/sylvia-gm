"""Generate reference vectors for the GM opening request (`ProviderSessionAdapter._messages`).

Run from the repository root with the snapshot's interpreter, after building the comparison
tool (`cargo build -p sylvia-compare`):

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_opening_messages_vectors.py

It records two real openings with the frozen Python owner, through `sylvia-compare` and the
stand-in model, and traces the opening preflight (`trace_opening_preflight.py`):

- `ruleless`: the `ruleless-turn` session (No Ruleset);
- `srd`: `srd-opening.session.json` (SRD Lite, Severed Lands, The Rift Awakening).

Each vector holds the adapter's inputs, the `SessionProviderInput`, every owner read the
request assembly made with its arguments and full result, and the messages `_messages`
returned with each one's ledger tags. A second assembly of the same request with the ledger
mode forced (`ledger`) holds its own reads, messages and tags. The Rust test answers the reads
from these results, so the messages and tags must come out equal. Times are replaced by one fixed value everywhere, so a rerun differs only where the
owner's identities do. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "opening_messages_vectors.json"
TRACE = HERE / "opening_preflight_trace.jsonl"
SESSIONS = {
    "ruleless": ROOT / "crates/sylvia-compare/sessions/ruleless-turn.session.json",
    "srd": HERE / "srd-opening.session.json",
}
TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
READS = {
    "session_context", "model_context_owner", "player_action_context",
    "player_action_response_context", "action_sequence_response_results", "canonical_replay",
    "ruleset_gm_operating_guide", "model_context_snapshot", "campaign_knowledge_catalog",
    "package_text_file", "snapshot", "campaign_construction_basis",
}


def record(name: str, session: Path, compare: Path) -> dict[str, object]:
    TRACE.unlink(missing_ok=True)
    python = Path(sys.executable)
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            [str(compare), "record", "--session", str(session), "--out", str(Path(folder) / "out"),
             "--owner", str(python), "--arg", "-B", "--arg", str(HERE / "trace_opening_preflight.py")],
            check=True, cwd=ROOT, env={**os.environ, "SYLVIA_TRACE_LEDGER_MESSAGES": "1"},
        )
    rows = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    TRACE.unlink()
    adapter = next(row for row in rows if row["event"] == "adapter")
    messages = next(row for row in rows if row["event"] == "messages")
    # Only the reads made while the messages were assembled: those after the adapter was built.
    start = rows.index(adapter)
    end = rows.index(messages)
    ledger = next(row for row in rows if row["event"] == "ledger_messages")
    ledger_end = rows.index(ledger)

    def reads(window: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {"name": row["name"], "args": row["args"], "kwargs": row["kwargs"], "result": row["result"]}
            for row in window if row["event"] == "call" and row["name"] in READS
        ]

    return {
        "name": name,
        "adapter": adapter["kwargs"],
        "model": adapter["model"],
        "context_mode": messages["context_mode"],
        "input": messages["input"],
        "reads": reads(rows[start:end]),
        "messages": messages["result"],
        "tags": messages["tags"],
        "ledger": {
            "reads": reads(rows[end:ledger_end]),
            "messages": ledger["result"],
            "tags": ledger["tags"],
        },
    }


def main() -> None:
    compare = ROOT / "target" / "debug" / ("sylvia-compare.exe" if sys.platform == "win32"
                                          else "sylvia-compare")
    if not compare.is_file():
        raise SystemExit(f"build the comparison tool first: {compare}")
    cases = [record(name, session, compare) for name, session in SESSIONS.items()]
    text = json.dumps({"cases": cases}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(cases), "openings,", [len(case["messages"]) for case in cases], "messages,",
          len(text), "bytes")


if __name__ == "__main__":
    main()
