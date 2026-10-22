"""Representative real-runtime checks for TD material shot presentation."""

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

def _execute(action_number: int, *, material_pen: int, damage: int):
    parameters = default_manifest().seed_set()
    runtime = builder._runtime(parameters.values, {})
    runtime["tools"].pop("exercise_specialty_counterfactual")
    runtime_bytes = builder._canonical_json(runtime)
    spec = builder._tool_specs([], [])["fire_single"]
    mechanic = materialize_package_mechanic(
        package_id=builder.PACKAGE_ID, package_version=builder.PACKAGE_VERSION,
        package_sha256=hashlib.sha256(runtime_bytes).hexdigest(),
        declaration=ToolDeclaration(
            name="fire_single", capability=spec["capability"],
            input_schema=builder._schema(spec["parameters"]), state_predicates=(),
            turn_statuses=("open",), writers=("dm",),
        ), runtime_bytes=runtime_bytes,
    )
    state = builder._scenario("0" * 40, parameters.hash, parameters.values)["initial_state"]
    actor = state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    actor["weapon"]["material_pen"] = material_pen
    actor["weapon"]["damage"] = damage
    # Hold the attack roll above the hit threshold while varying material penetration.
    with patch.object(dice._rng, "randint", return_value=10):
        outcome = mechanic(MechanicContext(
            campaign_id="td-material-shot", turn_id="turn",
            action_id=f"action-{action_number}", state=state,
            parameters={
                "attacker": "vesper", "target": "hostile_alpha",
                "aim_point": [9.5, 2.5, 0.5], "critical_effect": "DEVASTATING_HIT",
            },
        ))
    for mutation in outcome.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = thaw_json_value(mutation.value)
    return outcome.result, state


def test_stopped_projectile_ends_at_ordered_stop_contact():
    result, state = _execute(2, material_pen=0, damage=5)
    attack = state[f"{builder.PACKAGE_ID}.state"]["last_attack"]
    assert result["shot_effects"]["unavailable"] is False
    assert len(result["shot_effects"]["shots"]) == 1
    shot = result["shot_effects"]["shots"][0]
    assert attack["material_outcome"] == "STOP"
    assert attack["target_reached"] is False
    assert [contact["cue"] for contact in shot["contacts"]] == ["stop"]
    assert shot["destination"] == shot["contacts"][-1]["point"]
    assert shot["contacts"][0]["point"][0] == 6


def test_penetrating_projectile_preserves_order_and_destroys_cover():
    result, state = _execute(2, material_pen=10, damage=20)
    package = state[f"{builder.PACKAGE_ID}.state"]
    attack = package["last_attack"]
    assert result["shot_effects"]["unavailable"] is False
    assert len(result["shot_effects"]["shots"]) == 1
    shot = result["shot_effects"]["shots"][0]
    cover = next(item for item in package["spatial_scene"]["geometry"]
                 if item["id"] == "partial-cover")
    assert attack["target_reached"] is True
    assert attack["material_outcome"] == "TARGET"
    assert [contact["cue"] for contact in shot["contacts"]] == ["penetration", "character"]
    assert shot["destination"] == shot["contacts"][-1]["point"]
    assert attack["cover_hp_after"] == 0
    assert cover["active"] is False
    assert cover["movement"] == "passable"
    assert cover["visibility"] == "clear"
    assert shot["contacts"][0]["point"][0] < shot["contacts"][1]["point"][0]
