"""Generate player-action marker vectors for sylvia_domain::player_actions from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_player_action_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

`origins` records `player_control_origin(source, expected_revision=..., selection=...,
session_id=...)` for every control kind and for malformed sources (wrong keys, identities that
are blank, padded or not text, indexes and sequences at and past their bounds, booleans and
floats where an `int` is required). An accepted origin records its key order too.

`consequences` records `valid_player_action_consequence(owned_payload(payload),
expected_revision=...)` for the markers the owner writes and for every single-key change to
them: each key removed, each value replaced by an odd JSON value, an extra key, and a different
expected revision. A payload goes through `owned_payload` first, as a stored payload does.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from sylvia_gm.application.player_actions import (
    PLAYER_ACTION_ORIGIN,
    player_control_origin,
    valid_player_action_consequence,
)
from sylvia_gm.domain.authority import owned_payload, thaw_json_value

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "player_action_vectors.json"

# 2**63 fits JSON's unsigned 64-bit range but not i64: it must compare as a Python int.
WIDE = 2**63
ODD = [None, True, False, 0, 1, -1, 7, 8, 63, 64, 1.0, 2.5, WIDE, "", " ", " a", "a ", "a", "x\ny", [],
       {}, ["a"], {"k": "v"}]
SOURCES = [
    {"kind": "panel", "surface_id": "surface.one", "action_id": "act"},
    {"kind": "result_card", "action_id": "card", "button_index": 0},
    {"kind": "result_card", "action_id": "card", "button_index": 7},
    {"kind": "mechanics_choice", "action_id": "a", "choice_id": "c", "choice_sequence": 0, "option_id": "o",
     "resolution_id": "r"},
    {"kind": "mechanics_choice", "action_id": "a", "choice_id": "c", "choice_sequence": 63, "option_id": "o",
     "resolution_id": "r"},
]
SELECTIONS = [None, {}, {"target": "goblin", "n": 2}]
SESSIONS = [None, "session-1"]
POLICIES = [{"confirmation": "none", "response": "on_request"},
            {"confirmation": "required", "response": "automatic"}]


def outcome(call) -> dict:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001 - every exception class is behaviour here
        return {"raise": type(error).__name__, "message": str(error)}
    plain = thaw_json_value(result)
    return {"ok": plain, "keys": list(plain)}


def origin_cases() -> list[dict]:
    sources: list[object] = [copy.deepcopy(source) for source in SOURCES]
    sources += [None, "panel", [], {}, {"kind": "panel"}, {"kind": "bogus", "action_id": "a"}]
    for base in SOURCES:
        for key in base:
            removed = {k: v for k, v in base.items() if k != key}
            sources.append(removed)
            if key != "kind":
                for value in ODD:
                    sources.append({**base, key: value})
        sources.append({**base, "extra": 1})
    cases = []
    for index, source in enumerate(sources):
        selection = SELECTIONS[index % len(SELECTIONS)]
        session_id = SESSIONS[index % len(SESSIONS)]
        revision = index % 4
        cases.append({
            "source": source, "expected_revision": revision, "selection": selection, "session_id": session_id,
            "result": outcome(lambda: player_control_origin(
                source, expected_revision=revision, selection=selection, session_id=session_id)),
        })
    return cases


def markers() -> list[dict]:
    found = []
    for index, source in enumerate(SOURCES):
        for selection in SELECTIONS:
            for session_id in SESSIONS:
                origin = thaw_json_value(player_control_origin(
                    source, expected_revision=5, selection=selection, session_id=session_id))
                found.append({"kind": PLAYER_ACTION_ORIGIN,
                              "origin": {**origin, "policy": POLICIES[index % len(POLICIES)]}})
    return found


def consequence_cases() -> list[dict]:
    payloads = []
    for marker in markers():
        payloads.append(marker)
        payloads.append({**marker, "extra": 1})
        payloads.append({"origin": marker["origin"]})
        payloads.append({**marker, "kind": "other"})
        for value in ODD:
            payloads.append({**marker, "origin": value} if not isinstance(value, dict) else {**marker, "origin": {}})
        origin = marker["origin"]
        for key in list(origin) + ["extra"]:
            payloads.append({**marker, "origin": {k: v for k, v in origin.items() if k != key}})
            for value in ODD:
                payloads.append({**marker, "origin": {**origin, key: value}})
        for key in ("confirmation", "response", "extra"):
            for value in ["none", "required", "on_request", "automatic", None, 1, ""]:
                payloads.append({**marker, "origin": {**origin, "policy": {**origin["policy"], key: value}}})
    # Unique by content, keeping the first.
    seen, unique = set(), []
    for payload in payloads:
        text = json.dumps(payload, sort_keys=False)
        if text not in seen:
            seen.add(text)
            unique.append(payload)
    cases = []
    for payload in unique:
        try:
            owned = owned_payload(payload)
        except (TypeError, ValueError):
            continue  # not storable, so never reaches the verifier
        for expected in (5, 4):
            cases.append({"payload": payload, "expected_revision": expected,
                          "valid": valid_player_action_consequence(owned, expected_revision=expected)})
    return cases


def main() -> int:
    origins, consequences = origin_cases(), consequence_cases()
    document = {"python": sys.version.split()[0], "PLAYER_ACTION_ORIGIN": PLAYER_ACTION_ORIGIN,
                "origins": origins, "consequences": consequences}
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True) + "\n").encode("ascii"))
    accepted = sum("ok" in case["result"] for case in origins)
    valid = sum(case["valid"] for case in consequences)
    print(f"{len(origins)} origins ({accepted} accepted), {len(consequences)} consequences ({valid} valid)"
          f" -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
