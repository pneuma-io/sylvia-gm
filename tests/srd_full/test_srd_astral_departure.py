"""A genuine paid Plane Shift reunites the exact physical body at native Travel's mutation."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from scripts.srd_full.srd_astral_projection import POSSESSION_FIELDS, _lists, bind_departure_consumers
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_spell_exile_composition import _reachable
from scripts.srd_full.srd_spell_material_stock import bind_material_stock
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_astral_projection as original
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity


def _case():
    state, data, params = original._case()
    actor = state[f"{SRD}.entities"]["hero"]
    actor["inventory"] += ["rope", "material_fork"]
    actor["spell_materials"].append(
        {
            "id": "fork",
            "inventory_item": "material_fork",
            "kind": "specific",
            "quantity": 1,
            "value_cp": 25000,
            "components": ["plane_shift.1"],
            "attuned_plane": "material",
        }
    )
    actor["plane_shift_linked_hands"] = ["hero"]
    actor["prepared_spells"].append("plane_shift")
    data["spellcasting_grants"][0]["spells"].append("plane_shift")
    data["wizard_spellbook"].append("plane_shift")
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(
            "material-arrival",
            [200, 200, 0],
            is_object=True,
            movement="passable",
            plane="material",
        )
    )
    return state, data, params


def test_paid_public_departure_transports_original_body_stocks_reopens_and_retries(
    tmp_path, monkeypatch
):
    parent = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-astral-departure/retained-native-parent.yaml")
    ).read_bytes()
    assert sha256(parent).hexdigest() == (
        "fe066d1251fb874d6cb62f0fcb8eb925d4db40d56df0a3c8a8831158ce2624bc"
    )
    parsed = _load_runtime(parent.decode())
    monkeypatch.setattr(original, "source_runtime", lambda: (deepcopy(parsed), parent))
    original._runtime.cache_clear()
    raw, encoded = original._runtime()
    node = "move_srd_travel_subject_node"
    mutation_before = next(e for e in raw["tools"][node]["effects"] if "spatial_mutation" in e)
    text = bind_departure_consumers(bind_material_stock(encoded.decode()))
    assert bind_departure_consumers(text) == text
    runtime = _load_runtime(text)
    assert (
        next(e for e in runtime["tools"][node]["effects"] if "spatial_mutation" in e)
        == mutation_before
    )
    graph = {
        name: {e["call"] for effects in _lists(tool["effects"]) for e in effects if "call" in e}
        for name, tool in runtime["tools"].items()
    }
    assert not _reachable(graph, {"prepare_srd_astral_departure"}) & {
        node,
        "move_srd_travel_subject",
    }
    monkeypatch.setattr(original, "_runtime", lambda: (runtime, compact_runtime(text).encode()))
    original._mechanic.cache_clear()
    original._archive.cache_clear()
    monkeypatch.setattr(budget, "_mechanic", original._mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    execute = package_runtime.execute_tool
    execute_effect = _effect_executor._execute_effect
    relocations = []

    def observed_effect(effect, context, *args, **kwargs):
        if context.current_tool == node and "spatial_mutation" in effect:
            relocations.append(
                {
                    "subject": context.vars["_travel_mover"]["id"],
                    "destination": list(context.params["destination"]),
                }
            )
        return execute_effect(effect, context, *args, **kwargs)

    monkeypatch.setattr(_effect_executor, "_execute_effect", observed_effect)

    def diagnosed(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)
    monkeypatch.setattr(public, "bundled_srd_archive", original._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case())
    database = tmp_path / "astral-departure.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "astral_projection")
        began, begin_input = _act(owner, "begin_srd_spell_cast", **original._begin_values(params))
        assert began.status is ActionStatus.COMMITTED, began.result
        progress, progress_input = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progress.status is ActionStatus.COMMITTED, progress.result
        projection, projection_input = _act(owner, "cast_spell_outside_combat", **params)
        assert projection.status is ActionStatus.COMMITTED, projection.result
        saved = owner.snapshot("casting")
        actor = saved.state[f"{SRD}.entities"]["hero"]
        source_id = actor["astral_projection_source_id"]
        source = saved.state[f"{SRD}.state"]["astral_projection_sources"][source_id]
        assert actor["spell_materials"][-1]["attuned_plane"] == "material"
        original_stock = {
            field: deepcopy(saved.state[f"{SRD}.entities"][source["body_id"]].get(field))
            for field in POSSESSION_FIELDS
        }
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        travel, travel_input = _act(
            owner,
            "cast_spell_outside_combat",
            **(
                params
                | {
                    "spell": "plane_shift",
                    "target": "hero",
                    "slot_resource": "spell_slot_7",
                    "material_item_ids": ["fork"],
                    "travel_destination_subject": "material-arrival",
                }
            ),
        )
        assert travel.status is ActionStatus.COMMITTED, travel.result
        final = owner.snapshot("casting")
        actor = final.state[f"{SRD}.entities"]["hero"]
        assert actor["astral_projection_role"] is None
        assert actor["astral_projection_source_id"] is None
        assert {field: actor.get(field) for field in POSSESSION_FIELDS} == original_stock
        assert actor["conditions"] == [] and actor["hp"] == 40
        assert actor["resources"]["spell_slot_7"] == 2
        assert actor["resources"]["spell_slot_9"] == 2
        assert (
            final.state[f"{SRD}.state"]["astral_projection_sources"][source_id]["installed"]
            is False
        )
        scene = final.state[f"{SRD}.state"]["spatial_scene"]["entities"]
        body = next(r for r in scene if r["id"] == source["body_scene_id"])
        form = next(r for r in scene if r["id"] == source["form_scene_id"])
        assert len(relocations) == 2
        assert relocations[0]["subject"] == source["form_scene_id"]
        assert relocations[1]["subject"] == source["body_scene_id"]
        assert body["position"] == relocations[1]["destination"]
        assert body["metadata"]["plane"] == "material"
        assert body["metadata"]["participant_ref"] == "hero" and not form["active"]
        assert body["metadata"]["opaque_marker"] == "keep"
        for command, receipt in (
            (begin_input, began),
            (progress_input, progress),
            (projection_input, projection),
            (travel_input, travel),
        ):
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == final
        assert len(relocations) == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert len(relocations) == 2
        assert owner.execute_action(travel_input) == travel
        assert owner.snapshot("casting") == final
