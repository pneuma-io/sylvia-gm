"""Focused execution checks for every non-multi TD shot presentation surface."""

import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balance_suite.parameters import default_manifest
from sylvia_ruleset import builder
from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import MechanicContext, thaw_json_value
from sylvia_gm.domain.package_library import ToolDeclaration

class Harness:
    def __init__(self):
        parameters = default_manifest().seed_set()
        runtime = builder._runtime(parameters.values, {})
        runtime["tools"].pop("exercise_specialty_counterfactual")
        self.runtime_bytes = builder._canonical_json(runtime)
        self.specs = builder._tool_specs([], [])
        self.state = builder._scenario("0" * 40, parameters.hash, parameters.values)["initial_state"]
        self.sequence = 0

    def execute(self, tool_name, parameters):
        spec = self.specs[tool_name]
        mechanic = materialize_package_mechanic(
            package_id=builder.PACKAGE_ID,
            package_version=builder.PACKAGE_VERSION,
            package_sha256=hashlib.sha256(self.runtime_bytes).hexdigest(),
            declaration=ToolDeclaration(
                name=tool_name, capability=spec["capability"],
                input_schema=builder._schema(spec["parameters"]), state_predicates=(),
                turn_statuses=("open",), writers=("dm",),
            ), runtime_bytes=self.runtime_bytes,
        )
        self.sequence += 1
        outcome = mechanic(MechanicContext(
            campaign_id="td-shot-reactions", turn_id="turn",
            action_id=f"action-{self.sequence}", state=self.state, parameters=parameters,
        ))
        for mutation in outcome.mutations:
            self.state[f"{mutation.namespace}.{mutation.key}"] = thaw_json_value(mutation.value)
        return outcome.result


def test_single_cover_intercept_presents_cover_contact_without_unresolved_state_expression():
    harness = Harness()
    # A six plus this actor's eight meets the open threshold, below the covered threshold.
    with patch.object(dice._rng, "randint", return_value=6):
        observed = harness.execute("fire_single", {
            "attacker": "vesper", "target": "hostile_alpha",
            "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT",
        })
    attack = harness.state[f"{builder.PACKAGE_ID}.state"]["last_attack"]
    assert attack["open_difficulty"] <= attack["total"] < attack["actual_difficulty"]
    assert observed["context"]["_card_attack_outcome"] == "COVER_INTERCEPT"
    assert observed["shot_effects"]["unavailable"] is False
    assert len(observed["shot_effects"]["shots"]) == 1
    assert observed["shot_effects"]["shots"][0]["miss"] is False
    assert observed["shot_effects"]["shots"][0]["contacts"][0]["cue"] == "cover"


def _trigger(first_effect):
    harness = Harness()
    package = harness.state[f"{builder.PACKAGE_ID}.state"]
    entities = harness.state[f"{builder.PACKAGE_ID}.entities"]
    package["active_side"] = "hostiles"
    if first_effect == "OVERWATCH":
        package["prepared"]["overwatch"].update(
            active=True, owner="vesper", zone_id="overwatch-lane", shots=1)
    else:
        package["prepared"]["suppression"].update(
            active=True, owner="hex", zone_id="suppression-lane", capacity=1)
    entities["hostile_alpha"]["ap"] = entities["hostile_alpha"]["max_ap"]
    result = harness.execute("resolve_trigger_crossing", {
        "actor": "hostile_alpha", "overwatcher": "vesper", "suppressor": "hex",
        "destination": [7, 0], "first_effect": first_effect,
        "overwatch_critical_effect": "DEVASTATING_HIT",
        "suppression_critical_effect": "DEVASTATING_HIT",
    })
    assert harness.state[f"{builder.PACKAGE_ID}.state"]["network_motion"]["hostile_alpha"] == 1
    return result


def test_overwatch_and_suppression_reaction_fire_each_project_one_shot():
    for first_effect in ("OVERWATCH", "SUPPRESSION"):
        result = _trigger(first_effect)
        assert result["shot_effects"]["unavailable"] is False
        assert len(result["shot_effects"]["shots"]) == 1


def test_autodrone_fire_projects_one_resolved_shot():
    harness = Harness()
    result = harness.execute("autodrone_fire", {
        "actor": "autodrone_alpha", "target": "drone_target",
        "aim_point": [31.5, 30.5, 0.5],
    })
    assert result["shot_effects"]["unavailable"] is False
    assert len(result["shot_effects"]["shots"]) == 1
