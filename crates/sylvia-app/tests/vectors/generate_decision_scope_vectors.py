"""Generate decision-scope and actor-decision vectors for sylvia-app from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_decision_scope_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a campaign (its id, revision, state, participants and primary package), a
declaration and, for actor decisions, a turn id and tool schemas. The functions only read those
attributes, so the campaign is a plain stand-in object. The record is each public function's
result (`project_model_decision_scope`, `project_campaign_preparation_scope`,
`project_player_control_scope`, `player_viewer_ids`, `public_actor_observation`,
`project_actor_decision`) or the exception's class and message.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

from sylvia_gm.application import actor_decisions, decision_scope
from sylvia_gm.domain.authority import PackageRole, thaw_json_value

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "decision_scope_vectors.json"
NS = "pkg.demo"


def campaign(case: dict) -> SimpleNamespace:
    return SimpleNamespace(
        campaign_id=case["campaign_id"], revision=case["revision"], state=copy.deepcopy(case["state"]),
        participants=[SimpleNamespace(participant_id=p["participant_id"], kind=p["kind"], package_id=p["package_id"],
                                      data=copy.deepcopy(p["data"])) for p in case["participants"]],
        package_lock=SimpleNamespace(entries=[SimpleNamespace(package_id=e["package_id"], version=e["version"],
                                                              sha256=e["sha256"], role=PackageRole(e["role"]))
                                              for e in case["package_lock"]]),
    )


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if hasattr(value, "value") and type(value).__module__.startswith("sylvia_gm") and not isinstance(value, (str, int)):
        return value.value
    if isinstance(value, (frozenset, set)):
        return sorted(value)
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, str) or value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "value"):
        return value.value
    return thaw_json_value(value)


def call(function, *args, **kwargs) -> dict:
    try:
        return {"result": plain(function(*args, **kwargs))}
    except Exception as error:  # noqa: BLE001 - every exception Python lets through
        return {"error": [type(error).__name__, str(error)]}


def ref(key: str) -> dict:
    return {"namespace": NS, "key": key}


SIDE = {"decision_owner": ref("game.owner"), "active_side": ref("game.side"), "eligible_actors": ref("game.eligible"),
        "focused_actor": ref("game.focus"), "actor_entities": ref("entities")}
LEGACY = {"active_actor": ref("game.active"), "actor_entities": ref("entities"),
          "participant_control": {"field": "control", "player_values": ["player", 1]}, "default_owner": "gm"}
ISOLATED = {"instructions": "Play only this seat.", "observation_path": ["game"], "persona_path": ["persona"],
            "actions_path": ["actions"], "public_observation": ref("public.view")}
TOOLS = {"play": {"type": "object", "properties": {"tile": {"type": "integer", "minimum": 0}}, "required": ["tile"],
                  "additionalProperties": False},
         "pass": {"type": "object", "properties": {}, "additionalProperties": False}}


def actor(rng: random.Random, identity: str) -> dict:
    data = {"name": rng.choice([identity.title(), "", "  ", None, 5]), "hp": rng.randint(1, 9),
            "game": rng.choice([{"hand": [1, 2]}] * 4 + [{"hand": []}, [], "x"]),
            "persona": rng.choice(["Calm"] * 4 + ["", 3, "é" * 10, "x" * 8193]),
            "actions": rng.choice([[{"id": "a1", "label": "Play 1", "operation": "play", "arguments": {"tile": 1}},
                                    {"id": "a2", "label": "Pass", "operation": "pass", "arguments": {}}]] * 4 + [
                [{"id": "a1", "label": "Play 1", "operation": "play", "arguments": {"tile": 1}},
                 {"id": "a2", "label": "Pass", "operation": "pass", "arguments": {}}],
                [{"id": "a1", "label": "Play", "operation": "play", "arguments": {"tile": -1}}],
                [{"id": "a1", "label": "Play", "operation": "fly", "arguments": {}}],
                [{"id": "a1", "label": "Play", "operation": "pass", "arguments": {}},
                 {"id": "a1", "label": "Again", "operation": "pass", "arguments": {}}],
                [{"id": "a1", "label": "Play", "operation": "pass"}], [], "x",
            ]),
            "control": rng.choice(["player", "gm", 1, True, None]), "seat": rng.choice(["p.east", "p.west", None, 7]),
            "prep": rng.choice([None, [], [{"kind": "level"}], [{"kind": "a"}, {"kind": "b"}], "x", [1], [{}] * 65])}
    return {key: value for key, value in data.items() if value is not None or rng.random() < 0.5}


def random_case(rng: random.Random) -> dict:
    ids = ["east", "west", "north", "south"]
    entities = {identity: actor(rng, identity) for identity in ids if rng.random() < 0.85}
    if rng.random() < 0.1:
        entities["ghost"] = "not an object"
    eligible = rng.choice([ids[:2], ids, ["east"], [], ["east", "east"], ["east", 3], ["", "west"], "east", None,
                           ["east"] * 129, ["nobody"]])
    game = {"owner": rng.choice(["gm", "gm", "gm", "player", "x", None, 1]), "side": rng.choice(["north", "", None, 3, "south"]),
            "eligible": eligible, "focus": rng.choice([None, "east", "west", "nobody", 4]),
            "active": rng.choice(["east", "west", "", None, 3, "nobody"])}
    state = {f"{NS}.game": game, f"{NS}.entities": entities if rng.random() < 0.9 else rng.choice([[], "x"]),
             f"{NS}.public": {"view": rng.choice([{"table": [1]}, [], "x"])} if rng.random() < 0.8 else rng.choice([None, 3])}
    if rng.random() < 0.15:
        state[f"{NS}.game.side"] = "exact"
    participants = []
    for identity in rng.sample(ids + ["p.east"], rng.randint(0, 4)):
        participants.append({"participant_id": identity, "kind": rng.choice(["character", "seat"]),
                             "package_id": rng.choice([NS, NS, "pkg.other"]),
                             "data": {key: value for key, value in {"name": rng.choice(["Anna", "", None, 7]),
                                                                    "control": rng.choice(["player", "gm", 1.0, None])}.items()
                                      if value is not None or rng.random() < 0.5}})
    kind = rng.choice(["side", "legacy", "legacy-field", "side-prep", "legacy-prep", "none"])
    if kind.startswith("side"):
        declared = copy.deepcopy(SIDE)
    elif kind.startswith("legacy"):
        declared = copy.deepcopy(LEGACY)
        if kind == "legacy-field":
            declared["participant_control"]["actor_participant_field"] = "seat"
    else:
        declared = {"actor_entities": ref("entities"), "active_actor": ref("game.missing"),
                    "participant_control": {"field": "control", "player_values": ["player"]}, "default_owner": "gm"}
    if kind.endswith("prep"):
        declared["preparation"] = {"queue_field": "prep", "title": "Level up"}
    if rng.random() < 0.3:
        declared["participant_control"] = {"field": "control", "player_values": rng.choice([["player"], [True], []])}
    declared.update(ISOLATED)
    return {"campaign_id": "camp-1", "revision": rng.randint(0, 40), "state": state, "participants": participants,
            "package_lock": [{"package_id": NS, "version": "1.0.0", "sha256": "a" * 64, "role": "primary"}],
            "declared": declared, "turn_id": "turn-7"}


def record(case: dict) -> dict:
    snapshot, declared = campaign(case), case["declared"]
    out = {
        "scope": call(decision_scope.project_model_decision_scope, snapshot, declared),
        "preparation": call(decision_scope.project_campaign_preparation_scope, snapshot, declared),
        "control": call(decision_scope.project_player_control_scope, snapshot, declared),
        "viewers": call(decision_scope.player_viewer_ids, snapshot, declared),
        "public": call(actor_decisions.public_actor_observation, snapshot, declared),
    }
    try:
        boundary = decision_scope.project_model_decision_scope(snapshot, declared)
    except Exception:  # noqa: BLE001 - recorded above
        boundary = None
    decision = call(actor_decisions.project_actor_decision, snapshot, boundary, declared, turn_id=case["turn_id"],
                    tool_schemas=TOOLS)
    if "result" in decision and decision["result"] is not None:
        decision["result"]["observation_payload"] = plain(
            actor_decisions.project_actor_decision(snapshot, boundary, declared, turn_id=case["turn_id"],
                                                   tool_schemas=TOOLS).observation_payload())
    out["decision"] = decision
    return out


def main() -> int:
    rng = random.Random(4812)
    cases = []
    while len(cases) < 3000:
        case = random_case(rng)
        cases.append({**case, **record(case), "tools": TOOLS})
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "cases": cases}, ensure_ascii=True, allow_nan=False)
                        + "\n").encode("ascii"))
    found = sum(1 for case in cases if case["scope"].get("result"))
    decisions = sum(1 for case in cases if case["decision"].get("result"))
    print(f"{len(cases)} cases, {found} with a scope, {decisions} with an actor decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
