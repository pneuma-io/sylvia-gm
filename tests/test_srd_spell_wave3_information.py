"""Information spells prove their source-specific later consumers against the admitted scene."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_information_spells import PROFILES, validate_profiles
from scripts.srd_lite_catalogue import SELECTED

from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_extended_casting import _begin
from tests.test_srd_spell_result_cards import SRD, _spell_case

ROOT = Path(__file__).resolve().parents[1]


def _entity(subject, point, **metadata):
    return {
        "id": subject,
        "map_id": "information",
        "position": point,
        "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
        "height": 6,
        "facing": [1, 0],
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"plane": "material", "lighting": "bright", **metadata},
    }


def _scene(*entities, geometry=None, zones=None):
    return {
        "format": 1,
        "map": {
            "id": "information",
            "revision": 0,
            "bounds": {"min": [-30000, -30000, -30000], "max": [30000, 30000, 30000]},
            "grid": {"cell_size": 100, "unit": "ft", "origin": [-30000, -30000]},
            "layers": [{"id": "world", "min_elevation": -30000, "max_elevation": 30000}],
        },
        "entities": list(entities),
        "geometry": geometry or [],
        "zones": zones or [],
        "terrain": [],
    }


def _wall(thickness, material="stone", low_y=-10, high_y=10, kind="box"):
    volume = {"kind": "box", "min": [10, low_y, -10], "max": [10 + thickness, high_y, 10]}
    if kind == "cylinder":
        volume = {
            "kind": "cylinder",
            "center": [10 + thickness * 0.5, 0, -10],
            "radius": thickness * 0.5,
            "height": 20,
        }
    return {
        "id": "wall",
        "map_id": "information",
        "volume": volume,
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"material": material},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    }


def _case(spell, **choices):
    state, data, params = _spell_case(spell)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
            _entity(
                "guard",
                [20, 0, 0],
                participant_ref="guard",
                creature_type="fiend",
                magical_effect=True,
                spell_school="abjuration",
                poison=True,
                detected_kind="serpent venom",
                kind="serpent",
            ),
            _entity("orb", [25, 0, 0], is_object=True, kind="jewel", magical_effect=True),
        ),
    }
    params.update(
        information_kind="serpent",
        information_familiar=True,
        information_seen_close=True,
        information_obvious_place=True,
        information_position=[5, 0, 0],
        information_lighting="bright",
    )
    state[f"{SRD}.entities"]["hero"]["languages"] = ["common"]
    state[f"{SRD}.entities"]["guard"]["languages"] = ["infernal"]
    if spell == "tongues":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [5, 0, 0]
    if spell == "locate_object":
        params["information_kind"] = "jewel"
    if spell == "locate_animals_or_plants":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["creature_type"] = "beast"
    if spell == "clairvoyance":
        state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["components"].append(
            "clairvoyance.glass_eye"
        )
    params.update(choices)
    return state, data, params


def _free(state):
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, active_actor=None)


def _cast(spell, **choices):
    state, data, params = _case(spell, **choices)
    if spell == "clairvoyance":
        _free(state)
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    _run("cast_spell", state, participant_data=data, **params)
    return state, data, params


def _summary(result):
    return {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}


def _query(state, spell, **choices):
    return _summary(
        _run("query_srd_information_spell", state, caster_state="hero", spell=spell, **choices)
    )


@pytest.mark.parametrize("spell", [spell for spell in PROFILES if spell in SELECTED])
def test_exact_source_profiles_cast_through_the_actual_family(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/srd-lite/content/spell-mechanics.yaml").read_text()
    )
    declared = declaration["spells"][spell]
    assert declared["lite_profile"] == spell
    definition = {key: value for key, value in declared.items() if key != "lite_profile"}
    validate_profiles({spell: definition})
    bad = deepcopy(definition)
    bad["query_radius"] += 1
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({spell: bad})
    state, _, _ = _cast(spell)
    records = state[f"{SRD}.state"]["turn_effects"]
    if definition["effect_rounds"]:
        assert records[-1]["kind"] == "information_spell"
        assert (
            records[-1]["expires_tick"] - records[-1]["started_tick"]
            == definition["effect_rounds"] * 2
        )
    else:
        assert records == []
    source = json.loads(
        (ROOT / "rulesets/srd-lite/content/spell-mechanics-source.json").read_text()
    )["sources"][spell]
    assert source == "retained-srd-5.2.1-header"


def test_scene_relation_calculates_range_direction_sight_and_actual_plane():
    state, _, _ = _case("arcane_eye")
    result = _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )
    assert result["Distance"] == 20
    assert result["Direction"] == [1, 0, 0]
    assert result["Line of sight"] is True
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "ethereal"
    assert (
        _summary(
            _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
        )["Same plane"]
        is False
    )


def _zone(identifier, low, high, **metadata):
    return {
        "id": identifier,
        "map_id": "information",
        "shape": {"kind": "box", "min": low, "max": high},
        "metadata": {"plane": "material", **metadata},
    }


def _reject(tool, state, **params):
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _run(tool, state, **params)
    assert state == before


def _mind_blank(state, subject="guard"):
    state[f"{SRD}.entities"][subject]["has_timed_actor_defenses"] = True
    state[f"{SRD}.state"]["turn_effects"].append(
        {
            "kind": "timed_actor_defense",
            "spell": "mind_blank",
            "caster": subject,
            "target": subject,
            "started_tick": 0,
            "expires_tick": 28800,
            "concentration": False,
        }
    )


def test_arcane_eye_has_own_all_direction_darkvision_and_actual_bonus_action_movement():
    state, _, _ = _cast("arcane_eye")
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1].update(position=[-25, 0, 0])
    scene["entities"][1]["metadata"]["lighting"] = "darkness"
    assert "guard: seeing" in _query(state, "arcane_eye")["Information"]
    scene["entities"][1]["position"] = [-25.01, 0, 0]
    assert "guard: seeing" not in _query(state, "arcane_eye")["Information"]
    _run(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="arcane_eye",
        destination=[5, 10, 0],
    )
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    assert scene is not state[f"{SRD}.state"]["spatial_scene"]
    proxy = next(
        e
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["id"] == "hero:arcane_eye"
    )
    assert proxy["position"] == [5, 10, 0]
    _reject(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="arcane_eye",
        destination=[5, 11, 0],
    )


@pytest.mark.parametrize("gap,allowed", [(1 / 12, True), (0.999 / 12, False)])
def test_arcane_eye_one_inch_opening_is_calculated(gap, allowed):
    state, _, _ = _cast("arcane_eye")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    walls = [_wall(1, low_y=-10, high_y=-gap / 2), _wall(1, low_y=gap / 2, high_y=10)]
    walls[1]["id"] = "upper"
    scene["geometry"] = walls
    if allowed:
        _run(
            "operate_srd_information_proxy",
            state,
            caster_state="hero",
            spell="arcane_eye",
            destination=[15, 0, 0],
        )
    else:
        _reject(
            "operate_srd_information_proxy",
            state,
            caster_state="hero",
            spell="arcane_eye",
            destination=[15, 0, 0],
        )


def test_active_source_light_is_calculated_at_later_observation_and_respects_obstruction():
    state, _, _ = _case("arcane_eye")
    state[f"{SRD}.entities"]["guard"]["has_actor_spell_modifiers"] = True
    lasting = state[f"{SRD}.state"]
    lasting["turn_effects"].append(
        {
            "kind": "actor_spell_modifier",
            "caster": "guard",
            "target": "guard",
            "spell": "fire_shield",
            "mode": "fire_shield_warm",
            "started_tick": 0,
            "expires_tick": 100,
            "concentration": False,
        }
    )

    def light(point, plane="material"):
        return _run(
            "resolve_srd_information_light",
            state,
            point=point,
            plane=plane,
            ambient="darkness",
        ).result["context"]["_information_light"]

    assert light([10, 0, 0]) == "bright"
    assert light([0, 0, 0]) == "dim"
    assert light([-0.01, 0, 0]) == "darkness"
    assert light([10, 0, 0], "ethereal") == "darkness"
    lasting["spatial_scene"]["geometry"] = [_wall(0.1)]
    assert light([0, 0, 0]) == "darkness"
    lasting["spatial_scene"]["geometry"] = []
    lasting["lasting_tick"] = 100
    assert light([10, 0, 0]) == "darkness"


def test_animals_influence_uses_derived_pc_and_saved_npc_skills_in_exploration():
    state, data, _ = _cast("speak_with_animals")
    _free(state)
    entities = state[f"{SRD}.entities"]
    entities["guard"]["creature_type"] = "beast"
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            participant_data=data,
            listener_state="hero",
            speaker_state="guard",
            purpose="influence",
            actor="hero",
            influence_skill="performance",
        )
    )
    assert result["Influence outcome"] in {"success", "failure"}
    entities["hero"]["creature_kind"] = "stat_block"
    entities["hero"]["skill_check_modifiers"] = {"performance": 100}
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            listener_state="hero",
            speaker_state="guard",
            purpose="influence",
            influence_skill="performance",
            target_number=10,
        )
    )
    assert result["Influence outcome"] == "success"
    assert result["Influence total"] >= 101


def test_arcane_eye_clearance_uses_rounded_solid_corners():
    state, _, _ = _cast("arcane_eye")
    volume = {"kind": "box", "min": [10, 0.04, 0.04], "max": [11, 1, 1]}
    barrier = _wall(1)
    barrier["volume"] = volume
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [barrier]
    # Inflated bounds overlap, but Euclidean clearance exceeds the eye radius.
    _run(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="arcane_eye",
        destination=[15, 0, 0],
    )


def test_speak_animals_gives_actual_influence_skill_action_and_bounded_information():
    state, data, _ = _cast("speak_with_animals")
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "beast"
    state[f"{SRD}.combat"].update(action_available=True, normal_action_spent=False)
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            participant_data=data,
            listener_state="hero",
            speaker_state="guard",
            purpose="influence",
            actor="hero",
            influence_skill="performance",
        )
    )
    assert result["All Influence skills"] == "Yes"
    assert result["Influence outcome"] in {"success", "failure"}
    assert state[f"{SRD}.combat"]["action_available"] is False
    _reject(
        "communicate_srd_spell",
        state,
        listener_state="hero",
        speaker_state="guard",
        purpose="past_day",
        hours_ago=24.01,
    )


@pytest.mark.parametrize(
    "spell,later,choices,label,expected",
    [('arcane_eye',
  'query_srd_information_spell',
  {'spell': 'arcane_eye'},
  'Present',
  True)],
)
def test_public_information_cast_reopens_retries_then_consumes_saved_source(
    tmp_path,
    monkeypatch,
    spell,
    later,
    choices,
    label,
    expected,
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / f"{spell}.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, spell)
        mechanics = StatelessMcpMechanics(owner)
        advertised = mechanics.list_tools("casting")
        assert later in {tool.name for tool in advertised}
        schema = next(tool.input_schema for tool in advertised if tool.name == later)
        assert "lasting_state" not in schema["properties"]
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        mechanics = StatelessMcpMechanics(reopened)
        assert reopened.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command) == receipt
        if later != "communicate_srd_spell":
            choices = choices | {"caster_state": "hero"}
        consequence, later_command = _act(reopened, later, **choices)
        assert consequence.status is ActionStatus.COMMITTED
        summary = {
            row["label"]: row["value"] for row in consequence.result["result_card"]["summary"]
        }
        assert summary[label] == expected
        after = reopened.snapshot("casting")
        assert mechanics.execute_action(later_command) == consequence
        assert reopened.snapshot("casting").state == after.state


def test_public_npc_information_cast_uses_its_saved_daily_use_then_communicates(
    tmp_path, monkeypatch
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.test_srd_npc_spell_casting import _act, _prepare_mage

    def npc_case(spell):
        state, data, params = _case(spell)
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("mage", [0, 5, 0], participant_ref="mage", creature_type="humanoid")
        )
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", npc_case)
    database = tmp_path / "npc-information.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "speak_with_animals",
            target_overrides={"creature_type": "beast"},
        )
        _prepare_mage(owner, [{"spell": "speak_with_animals", "uses_per_day": 1}])
        params.pop("caster")
        params.pop("slot_resource")
        params["caster_state"] = "mage"
        receipt, command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][
                "speak_with_animals"
            ]
            == 0
        )
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        later, _ = _act(
            reopened,
            "communicate_srd_spell",
            listener_state="mage",
            speaker_state="guard",
            purpose="nearby_monsters",
        )
        summary = {row["label"]: row["value"] for row in later.result["result_card"]["summary"]}
        assert summary["Information available"] == "Yes"


def test_sensor_expiry_deactivates_its_actual_scene_entry_and_rejects_later_queries():
    state, _, _ = _cast("arcane_eye")
    _free(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    proxy = next(
        item
        for item in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if item["id"] == "hero:arcane_eye"
    )
    assert proxy["active"] is False
    _reject("query_srd_information_spell", state, caster_state="hero", spell="arcane_eye")
