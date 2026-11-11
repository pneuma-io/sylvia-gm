"""Execute TD suppression presentation through Sylvia's real runtime."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balance_suite.parameters import default_manifest
from sylvia_ruleset import builder
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import MechanicContext, thaw_json_value
from sylvia_gm.domain.package_library import ToolDeclaration


def _fixture():
    parameters = default_manifest().seed_set()
    runtime = builder._runtime(parameters.values, {})
    runtime["tools"].pop("exercise_specialty_counterfactual")
    runtime_bytes = builder._canonical_json(runtime)
    specs = builder._tool_specs([], [])
    state = builder._scenario("0" * 40, parameters.hash, parameters.values)["initial_state"]
    spec = specs["establish_suppression"]
    mechanic = materialize_package_mechanic(
        package_id=builder.PACKAGE_ID,
        package_version=builder.PACKAGE_VERSION,
        package_sha256=hashlib.sha256(runtime_bytes).hexdigest(),
        declaration=ToolDeclaration(
            name="establish_suppression",
            capability=spec["capability"],
            input_schema=builder._schema(spec["parameters"]),
            state_predicates=(),
            turn_statuses=("open",),
            writers=("dm",),
        ),
        runtime_bytes=runtime_bytes,
    )
    return mechanic, state


def _execute(mechanic, state):
    result = mechanic(MechanicContext(
        campaign_id="td-suppression-shot",
        turn_id="turn",
        action_id="action",
        state=state,
        parameters={"actor": "vesper", "zone_id": "suppression-lane", "rounds": 5},
    ))
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = thaw_json_value(mutation.value)
    return result.result


def test_visible_suppression_zone_gets_one_no_contact_cue_per_expended_round():
    mechanic, state = _fixture()
    actor = state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    actor["weapon"]["cyclic_rate_rpm"] = 600
    before_rounds = actor["weapon"]["magazine"]["rounds"]
    result = _execute(mechanic, state)
    shots = result["shot_effects"]["shots"]
    assert len(shots) == 5
    assert [shot["delay_ms"] for shot in shots] == [0, 100, 200, 300, 400]
    assert all(shot["destination"] == [6.5, 2.0, 1.25] for shot in shots)
    assert all(shot["miss"] is False and shot["contacts"] == [] for shot in shots)
    assert "suppression_zones" not in result["context"]
    assert "_fx_suppression_zone" not in result["context"]
    assert "_fx_suppression_center" not in result["context"]
    current = state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    assert current["weapon"]["magazine"]["rounds"] == before_rounds - 5


def test_hidden_suppression_zone_suppresses_cues_but_still_expends_rounds():
    mechanic, state = _fixture()
    package = state[f"{builder.PACKAGE_ID}.state"]
    zone = next(item for item in package["spatial_scene"]["zones"]
                if item["id"] == "suppression-lane")
    zone["metadata"].pop("player")
    actor = state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    actor["weapon"]["cyclic_rate_rpm"] = 600
    before_rounds = actor["weapon"]["magazine"]["rounds"]
    result = _execute(mechanic, state)
    assert result["shot_effects"]["shots"] == []
    assert result["shot_effects"]["unavailable"] is True
    current = state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    assert current["weapon"]["magazine"]["rounds"] == before_rounds - 5


def test_missing_suppression_cadence_records_unavailable_without_undoing_ammo():
    mechanic, state = _fixture()
    actor = state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    actor["weapon"]["cyclic_rate_rpm"] = None
    before_rounds = actor["weapon"]["magazine"]["rounds"]
    result = _execute(mechanic, state)
    assert result["shot_effects"]["shots"] == []
    assert result["shot_effects"]["unavailable"] is True
    current = state[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    assert current["weapon"]["magazine"]["rounds"] == before_rounds - 5
