"""Run the Python owner with the context ledger's pure functions traced, for the ledger vectors.

Launched by sylvia-compare as the Python owner (`python -B trace_ledger_functions.py`), with
`SYLVIA_GM_CONTEXT_MODE=ledger` so every GM request goes through the ledger. Each traced
function of `agent/_context_ledger.py` (or, named `loader:<name>` and `projection:<name>`, of
`_tool_loader.py` and `_fixed_tool_projection.py`, wrapped before anything imports them) is
replaced in the module's namespace (so calls from inside the module are traced too) by a wrapper that logs its arguments before the call and its
result, or the exception's class and message, after it. A message argument keeps its ledger
tags (`{"message": ..., "tags": ...}`). Writes JSON lines to the file `SYLVIA_LEDGER_TRACE`
names. `uuid.uuid4` is seeded for the whole process, so the dice an action's uuid seeds roll
the same on every run. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import threading
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

# The loader and the fixed projection are wrapped before anything imports them by name (the
# ledger binds the loader's functions at its own import).
from sylvia_gm.agent import _fixed_tool_projection as projection  # noqa: E402
from sylvia_gm.agent import _tool_loader as loader  # noqa: E402

TRACE_LOG = Path(os.environ["SYLVIA_LEDGER_TRACE"])
TRACED = [name for name in os.environ.get("SYLVIA_LEDGER_TRACED", "").split(",") if name]
MODULES = {"loader": loader, "projection": projection}


def plain(value: object) -> object:
    """A JSON copy of a traced value: a tagged message keeps its tags, sets become sorted lists,
    and `MechanicNames` its catalog and reserved names (what it is built from)."""
    if isinstance(value, loader.MechanicNames):
        return {"__names__": {"catalog": sorted(value._catalog_short), "reserved": sorted(value._reserved)}}
    if isinstance(value, ledger.JournalState):
        return {"__journal__": journal_fields(value)}
    if isinstance(value, ledger.Tagged):
        return {"__tagged__": {"message": plain(dict(value)), "tags": plain(value.ledger_tags)}}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted((plain(item) for item in value), key=repr)}
    if isinstance(value, ledger.WireFrame):
        return {"__frame__": {key: plain(item) for key, item in vars(value).items()}}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return {"__repr__": repr(value), "__class__": type(value).__name__}


def journal_fields(state: object) -> dict[str, object]:
    """A `JournalState`'s fields as JSON: integer keys as text, `present` as sorted pairs."""
    return {
        "epoch": state.epoch, "generation": state.generation, "entries": plain(state.entries),
        "head": state.head, "view": plain(state.view), "keyframe_bytes": state.keyframe_bytes,
        "update_bytes": state.update_bytes,
        "heads": {str(key): value for key, value in state.heads.items()},
        "present": sorted([label, digest] for label, digest in state.present),
        "native": {str(key): plain(value) for key, value in state.native.items()},
        "thinking_floor": state.thinking_floor, "last_prefix": plain(state.last_prefix),
        "last_toolset": plain(state.last_toolset),
    }


#: One writer at a time: the provider's stream is logged on the provider runtime's thread while
#: the turn logs its owner calls on its own, and appends from two handles can interleave on Windows.
TRACE_LOCK = threading.Lock()


def log(entry: dict[str, object]) -> None:
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with TRACE_LOCK, TRACE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line)


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


# `loader:name` and `projection:name` are traced in those modules, before the ledger imports.
for qualified in TRACED:
    module_name, _, function_name = qualified.rpartition(":")
    if module_name:
        module = MODULES[module_name]
        setattr(module, function_name, traced(qualified, getattr(module, function_name)))

import sylvia_gm.interfaces.owner_process as owner_process  # noqa: E402
from sylvia_gm.agent import _context_ledger as ledger  # noqa: E402

for qualified in TRACED:
    if ":" not in qualified:
        setattr(ledger, qualified, traced(qualified, getattr(ledger, qualified)))

if __name__ == "__main__":
    raise SystemExit(owner_process.main())
