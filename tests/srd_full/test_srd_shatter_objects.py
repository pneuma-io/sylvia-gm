"""A paid object-only Shatter uses one native Thunder roll and the shared HP receiver."""

from copy import deepcopy
from functools import lru_cache

from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_shatter_objects import bind_runtime_consumers
from scripts.srd_full.srd_spell_object_damage import bind_runtime_consumers as bind_object_damage
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene


@lru_cache
def _runtime():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    text = bind_runtime_consumers(bind_object_damage(text))
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


def test_paid_object_only_shatter_uses_shared_roll_and_preserves_magical_worn_objects(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    state, data, params = _spell_case("shatter")
    state[f"{budget.SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    scene = state[f"{budget.SRD}.state"]["spatial_scene"]
    objects = []
    for name, magic, worn in (
        ("crate", "nonmagical", None),
        ("worn", "nonmagical", "guard"),
        ("enchanted", "magical", None),
    ):
        body = deepcopy(scene["entities"][0])
        body.update(id=name, position=[10, 0, 0])
        body["metadata"] = {"plane": "material", "lighting": "bright"}
        if worn:
            body["metadata"]["worn_by"] = worn
        scene["entities"].append(body)
        budget._run(
            "prepare_srd_spell_object",
            state,
            object_id=name,
            material="wood",
            size="large",
            durability="resilient",
            magic_status=magic,
        )
        objects.append(name)
        scene = state[f"{budget.SRD}.state"]["spatial_scene"]
    profiles = {
        obj["id"]: deepcopy(obj["metadata"]["srd_spell_object"])
        for obj in scene["entities"]
        if obj["id"] in objects
    }
    rolls = []
    original = _effect_executor.dice_roll

    def record_roll(sides, count=1, modifier=0):
        result = original(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", record_roll)
    params.update(targets=[], shatter_object_targets=objects)
    result = budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    assert [(r.sides, r.count) for r in rolls] == [(8, 3)]
    after = {
        obj["id"]: obj["metadata"]["srd_spell_object"]
        for obj in state[f"{budget.SRD}.state"]["spatial_scene"]["entities"]
        if obj["id"] in objects
    }
    assert after["crate"]["hp"] == profiles["crate"]["hp"] - rolls[0].total
    assert after["worn"] == profiles["worn"]
    assert after["enchanted"] == profiles["enchanted"]
    assert state[f"{budget.SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    assert any(r["label"] == "crate" for r in result.result["result_card"]["details"])
