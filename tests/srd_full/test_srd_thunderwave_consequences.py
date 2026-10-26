"""A native paid Thunderwave moves only failed saves and actual unsecured objects."""

from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_source_relocation_node import bind_runtime_consumers as bind_relocation_node
from scripts.srd_full.srd_thunderwave_consequences import MOVE, bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene


@lru_cache
def _runtime():
    text = bind_runtime_consumers(
        bind_relocation_node((budget.ROOT / "runtime/ruleset.yaml").read_text())
    )
    assert _load_runtime(bind_runtime_consumers(text)) == _load_runtime(text)
    return text.encode(), _load_runtime(text)


@lru_cache
def _mechanic(name):
    encoded, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=budget.SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def test_paid_native_failure_object_push_and_sound_use_existing_receivers(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    state, data, params = _spell_case("thunderwave")
    data["classes"] = [{"id": "bard", "level": 17}]
    data["abilities"]["charisma"] = 18
    data["spellcasting_grants"] = [
        {"source": "bard", "ability": "charisma", "spells": ["thunderwave", "silence"]},
    ]
    data.pop("wizard_spellbook", None)
    entities = state[f"{budget.SRD}.entities"]
    entities["hero"]["prepared_spells"] = ["thunderwave", "silence"]
    for target, modifier in (("guard", -30), ("other", 30)):
        entities[target].update(hp=100, max_hp=100, temp_hp=0)
        entities[target]["saving_throw_modifiers"]["constitution"] = modifier
    state[f"{budget.SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    scene = state[f"{budget.SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["entities"][2]["position"] = [0, 10, 0]
    crate = deepcopy(scene["entities"][0])
    crate.update(id="crate", position=[5, 5, 0])
    crate["metadata"] = {"plane": "material", "is_object": True, "unsecured": True}
    scene["entities"].append(crate)
    combat_before = deepcopy(state[f"{budget.SRD}.combat"])
    params.update(targets=["guard", "other"], thunderwave_unsecured_objects=["crate"])
    result = budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    positions = {
        e["id"]: e["position"] for e in state[f"{budget.SRD}.state"]["spatial_scene"]["entities"]
    }
    assert positions["guard"] == [15, 0, 0]
    assert positions["other"] == [0, 10, 0]
    assert positions["crate"][0] == positions["crate"][1]
    assert positions["crate"][0] > 5
    assert sum((coordinate - 5) ** 2 for coordinate in positions["crate"][:2]) == pytest.approx(100)
    assert (
        state[f"{budget.SRD}.combat"]["movement_remaining"] == combat_before["movement_remaining"]
    )
    final_entities = state[f"{budget.SRD}.entities"]
    assert final_entities["guard"]["hp"] < final_entities["other"]["hp"]
    assert state[f"{budget.SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    receipt = state[f"{budget.SRD}.state"]["thunderwave_pending_motion"]
    assert receipt["consumed"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run(
            MOVE,
            state,
            subject="crate",
            destination=receipt["destination"],
            plane="material",
            teleport=False,
            thunderwave_receipt_id=receipt["receipt_id"],
        )
    assert state == before
    heard = budget._run("query_srd_thunderwave_sound", state, listener_state="other")
    assert heard.result["context"]["_tw_sound_audible"]
    _, _, silence_params = _spell_case("silence")
    silence_params.update(environment_origin=[0, 10, 3])
    assert (
        budget._run(
            "cast_spell_outside_combat", state, participant_data=data, **silence_params
        ).result["status"]
        == "committed"
    )
    quiet = budget._run("query_srd_thunderwave_sound", state, listener_state="other")
    assert not quiet.result["context"]["_tw_sound_audible"]
