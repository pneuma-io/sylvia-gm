"""The accepted full-SRD provider retains its genuine 2024 True Strike attack."""

import hashlib
from pathlib import Path
from random import Random

from sylvia_gm import dice
from tests import test_srd_spell_result_cards as result_cards
from tests.test_srd_action_budget import SRD, _run, _runtime_definition


def test_preserved_full_srd_true_strike_makes_the_2024_weapon_attack(monkeypatch):
    root = Path(__file__).parents[1] / "rulesets/srd-lite"
    runtime_bytes = (root / "runtime/ruleset.yaml").read_bytes()
    assert (
        hashlib.sha256(runtime_bytes).hexdigest()
        == "58650516cfd6cf90b52263a357bf186430366c28d1f240cbb776b029ce0c81cf"
    )
    # Reuse the existing C-backed loader of these exact retained bytes.
    definitions = _runtime_definition(runtime_bytes)["definitions"]["spells"]
    monkeypatch.setattr(result_cards, "_spell_definitions", lambda: definitions)
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = result_cards._spell_case("true_strike")
    state[SRD + ".entities"]["hero"]["spell_materials"][0]["inventory_item"] = "dagger"
    assert state[SRD + ".entities"]["hero"]["inventory"].count("dagger") == 1
    state[SRD + ".entities"]["hero"]["held_items"] = ["dagger"]
    state[SRD + ".state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": {
            "format": 1,
            "map": {
                "id": "full",
                "revision": 0,
                "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
                "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
            },
            "entities": [
                {
                    "id": identity,
                    "map_id": "full",
                    "position": position,
                    "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                    "height": 6,
                    "facing": [1, 0],
                    "active": True,
                    "movement": "blocking",
                    "visibility": "opaque",
                    "metadata": {
                        "participant_ref": identity,
                        "plane": "material",
                        "lighting": "bright",
                        "creature_type": "humanoid",
                    },
                }
                for identity, position in [
                    ("hero", [0, 0, 0]),
                    ("guard", [5, 0, 0]),
                    ("other", [-5, 0, 0]),
                ]
            ],
            "geometry": [],
            "zones": [],
            "terrain": [],
        },
    }
    _run(
        "record_srd_scene_hand_occupancy",
        state,
        participant_data=data,
        actor_state="hero",
        hand_capacity=2,
        holdings=[{"inventory_item": "dagger", "hand_indices": [0]}],
    )
    state[SRD + ".entities"]["guard"].update(armor_class=1, temp_hp=0, hp=1000, max_hp=1000)
    before = state[SRD + ".entities"]["guard"]["hp"]
    result = _run(
        "cast_spell",
        state,
        participant_data=data,
        **(
            parameters
            | {
                "true_strike_weapon": "dagger",
                "true_strike_attack_mode": "melee",
                "true_strike_two_handed": False,
                "true_strike_damage_choice": "radiant",
            }
        ),
    )
    assert result.result["status"] == "committed"
    assert state[SRD + ".entities"]["guard"]["hp"] < before
    assert state[SRD + ".entities"]["hero"].get("lite_true_strike") is None
    assert state[SRD + ".entities"]["hero"].get("concentration_spell_id") is None
