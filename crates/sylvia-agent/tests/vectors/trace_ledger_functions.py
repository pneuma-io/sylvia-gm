"""Run the Python owner with the context ledger's pure functions traced, for the ledger vectors.

Launched by sylvia-compare as the Python owner (`python -B trace_ledger_functions.py`), with
`SYLVIA_GM_CONTEXT_MODE=ledger` so every GM request goes through the ledger. Each traced
function of `agent/_context_ledger.py` is replaced in the module's namespace (so calls from
inside the module are traced too) by a wrapper that logs its arguments before the call and its
result, or the exception's class and message, after it. A message argument keeps its ledger
tags (`{"message": ..., "tags": ...}`). Writes JSON lines to the file `SYLVIA_LEDGER_TRACE`
names. `uuid.uuid4` is seeded for the whole process, so the dice an action's uuid seeds roll
the same on every run. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

# `uuid4()` from a fixed seed for the whole owner, before anything imports it: an action's dice
# are seeded from its uuid action id, so the traced rolls are the same on every run.
import random  # noqa: E402
import uuid  # noqa: E402

_UUIDS = random.Random(20261128)
uuid.uuid4 = lambda: uuid.UUID(int=_UUIDS.getrandbits(128), version=4)

import sylvia_gm.interfaces.owner_process as owner_process  # noqa: E402
from sylvia_gm.agent import _context_ledger as ledger  # noqa: E402

TRACE_LOG = Path(os.environ["SYLVIA_LEDGER_TRACE"])
TRACED = [name for name in os.environ.get("SYLVIA_LEDGER_TRACED", "").split(",") if name]


def plain(value: object) -> object:
    """A JSON copy of a traced value: a tagged message keeps its tags, sets become sorted lists."""
    if isinstance(value, ledger.Tagged):
        return {"__tagged__": {"message": plain(dict(value)), "tags": plain(value.ledger_tags)}}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted((plain(item) for item in value), key=repr)}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return {"__repr__": repr(value), "__class__": type(value).__name__}


def log(entry: dict[str, object]) -> None:
    with TRACE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def traced(name: str, function):  # noqa: ANN001, ANN202 - any module function
    def wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        entry: dict[str, object] = {"function": name, "args": plain(list(args)), "kwargs": plain(kwargs)}
        try:
            result = function(*args, **kwargs)
        except Exception as error:
            entry["raised"] = {"class": type(error).__name__, "message": str(error)}
            log(entry)
            raise
        entry["result"] = plain(result)
        log(entry)
        return result

    return wrapper


for function_name in TRACED:
    setattr(ledger, function_name, traced(function_name, getattr(ledger, function_name)))

if __name__ == "__main__":
    raise SystemExit(owner_process.main())
