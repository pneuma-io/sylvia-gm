"""Appearance spells cast authentic sources and consume them through sight, touch and writing."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_illusion_appearance import (
    PROFILES,
    PUBLIC_TOOLS,
    extend_runtime,
    validate_profiles,
)
from scripts.srd_full.srd_reaction_spells import finalize_pending_spell_guards
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_illusions import _case as _illusion_case
from tests.srd_full.test_srd_spell_illusions import _grant_other_spell, _summary
from tests.srd_full.test_srd_spell_illusions import _runtime as _image_runtime
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    _, content = _image_runtime()
    text = extend_runtime(content.decode())
    text = refresh_exploration_runtime(text)
    text = finalize_pending_spell_guards(text)
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])


def _spec(subject="hero", **changes):
    return {
        "subject": subject,
        "description": "A shorter traveler with a feathered hat",
        "height_offset_inches": -12,
        "limb_arrangement": "two arms and two legs",
        "willing": True,
        **changes,
    }


def _case(spell, **changes):
    state, data, parameters = _illusion_case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for entity in scene["entities"]:
        if entity["id"] in {"hero", "guard", "other"}:
            entity["metadata"]["basic_limb_arrangement"] = "two arms and two legs"
    scene["entities"].extend(
        [
            _entity("paper", [5, 0, 0], is_object=True, writable_surface=True),
            _entity("field", [0, 0, 0], natural_terrain=True, tactile_kind="smooth earth"),
        ]
    )
    field = next(e for e in scene["entities"] if e["id"] == "field")
    field["footprint"]["half_extents"] = [100, 100]
    field.update(movement="passable", visibility="clear", height=0.1)
    field["metadata"]["terrain_kind"] = "flat meadow"
    parameters.update(
        appearance_specs=[_spec()],
        appearance_position=[0, 0, 0],
        appearance_description="A natural rocky gully",
        appearance_terrain_kind="rocky gully",
        appearance_tactile_kind="rough rock",
        appearance_sound="Wind over rocks",
        appearance_smell="Damp stone",
        script_surface="paper",
        script_original="The door opens at dawn",
        script_readers=["other"],
    )
    parameters.update(changes)
    return state, data, parameters


def _begin(parameters):
    return {
        key: value
        for key, value in parameters.items()
        if key
        in {
            "caster",
            "caster_state",
            "participant_data",
            "spell",
            "slot_resource",
            "selected_class",
            "source_class",
            "casting_ability",
            "casting_level",
            "proficiency_bonus",
            "material_item_ids",
            "verbal_component_available",
            "somatic_component_available",
            "material_component_available",
            "casting_mode",
            "spellbook_readable",
        }
    }


def _cast(spell, **changes):
    state, data, parameters = _case(spell, **changes)
    if spell in {"illusory_script", "hallucinatory_terrain"}:
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=(110 if changes.get("casting_mode") == "ritual" else 10)
            if spell == "illusory_script"
            else 100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            spellbook_readable=changes.get("spellbook_readable", False),
        )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


@pytest.mark.parametrize("spell", PROFILES)
def test_actual_casts_install_printed_independent_lifetimes(spell):
    state, _, _ = _cast(spell)
    source = next(iter(state[f"{SRD}.state"]["appearance_spell_sources"].values()))
    assert source["spell"] == spell and source["installed"]
    assert source["expires_tick"] - source["started_tick"] == PROFILES[spell]["duration_rounds"] * 2
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") in {None, ""}


def test_actual_disguise_appearance_touch_and_expiry_preserve_body_and_other_observer():
    state, _, _ = _cast("disguise_self")
    scene = deepcopy(state[f"{SRD}.state"]["spatial_scene"])
    observed = _summary(
        _run("observe_srd_appearance", state, observer_state="other", subject="hero")
    )
    assert observed["Active illusion"] and not observed["Known illusion"]
    assert observed["Appearance"] == "A shorter traveler with a feathered hat"
    hero = next(e for e in scene["entities"] if e["id"] == "hero")
    assert observed["Apparent height"] == hero["height"] - 1
    _run("inspect_srd_appearance", state, observer_state="other", subject="hero")
    assert _summary(_run("observe_srd_appearance", state, observer_state="other", subject="hero"))[
        "Known illusion"
    ]
    assert not _summary(
        _run("observe_srd_appearance", state, observer_state="guard", subject="hero")
    )["Known illusion"]
    assert state[f"{SRD}.state"]["spatial_scene"] == scene
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert not _summary(
        _run("observe_srd_appearance", state, observer_state="other", subject="hero")
    )["Active illusion"]


@pytest.mark.parametrize(
    "changes", [{"height_offset_inches": 13}, {"limb_arrangement": "four legs"}]
)
def test_disguise_rejects_source_height_and_anatomy_before_commit(changes):
    state, data, parameters = _case("disguise_self", appearance_specs=[_spec(**changes)])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_actual_script_designated_reader_truesight_expiry_and_exact_dispel():
    state, _, _ = _cast("illusory_script")
    assert (
        _summary(_run("read_srd_illusory_script", state, observer_state="other", subject="paper"))[
            "Message"
        ]
        == "The door opens at dawn"
    )
    assert not _summary(
        _run("read_srd_illusory_script", state, observer_state="guard", subject="paper")
    )["Readable"]
    state[f"{SRD}.entities"]["guard"]["truesight_feet"] = 120
    assert (
        _summary(_run("read_srd_illusory_script", state, observer_state="guard", subject="paper"))[
            "Message"
        ]
        == "The door opens at dawn"
    )
    source = next(iter(state[f"{SRD}.state"]["appearance_spell_sources"].values()))
    _run("retire_srd_appearance_source", state, appearance_id=source["id"], reason="dispel")
    assert not _summary(
        _run("read_srd_illusory_script", state, observer_state="hero", subject="paper")
    )["Readable"]


@lru_cache
def _appearance_archive():
    """Admit the authored runtime and exact public schemas through normal archive admission."""
    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes

    primary, reference = _srd_drafts()
    record = deepcopy(dict(primary.record))
    payload = dict(primary.payloads)
    runtime, content = _runtime()
    payload["runtime/ruleset.yaml"] = content
    declarations = {d["name"]: d for d in record["tools"]}
    for name in PUBLIC_TOOLS:
        declarations[name] = {
            "name": name,
            "capability": "dnd.srd.5.2.1.spells",
            "input_schema": {},
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
        }
    for name, declaration in declarations.items():
        declaration["input_schema"] = _tool_schema(runtime["tools"][name], tool_name=name)
    record["tools"] = [declarations[name] for name in sorted(declarations)]
    content_record = json.loads(payload["content/content-declarations.json"])
    inclusion = next(
        row for row in content_record["inclusions"] if row["id"] == "srd-5.2.1-gameplay-runtime"
    )
    inclusion["links"]["tools"] = sorted(declarations)
    payload["content/content-declarations.json"] = canonical_json_bytes(content_record)
    return _archive((NormalizedPackageDraft(record, payload, 4), reference))


def test_public_disguise_discovery_derived_study_reopen_retry_and_expiry(tmp_path, monkeypatch):
    from sylvia_gm.agent._provider_session import _derive_mechanics_parameters
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "bundled_srd_archive", _appearance_archive)
    monkeypatch.setattr(public, "_spell_case", _case)
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "appearance.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "disguise_self",
            participant_overrides={
                "abilities": {
                    "strength": 10,
                    "dexterity": 10,
                    "constitution": 10,
                    "intelligence": 20,
                    "wisdom": 10,
                    "charisma": 10,
                }
            },
        )
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert names >= PUBLIC_TOOLS
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        observed, _ = _act(owner, "observe_srd_appearance", observer_state="hero", subject="hero")
        assert _summary(observed)["Active illusion"]
        runtime, _ = _runtime()
        schema = _tool_schema(
            runtime["tools"]["study_srd_appearance"], tool_name="study_srd_appearance"
        )
        derivations = {
            key: value["x-sylvia-gm-derived"]
            for key, value in schema["properties"].items()
            if "x-sylvia-gm-derived" in value
        }
        actual = _derive_mechanics_parameters(
            {"observer": "hero", "subject": "hero"},
            derivations,
            owner.snapshot("casting").participants,
        )
        studied, study_command = _act(owner, "study_srd_appearance", **actual)
        assert studied.status is ActionStatus.COMMITTED and _summary(studied)["Recognized"]
        known = owner.snapshot("casting")
        assert mechanics.execute_action(study_command) == studied
        assert owner.snapshot("casting").state == known.state
        _act(owner, "advance_srd_lasting_time", elapsed_rounds=600)
        ended, _ = _act(owner, "observe_srd_appearance", observer_state="hero", subject="hero")
        assert not _summary(ended)["Active illusion"]
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"]
            == 2
        )


def test_owned_emitter_repeat_profiles_and_inactive_cost_shape():
    runtime, content = _runtime()
    assert _load_runtime(extend_runtime(content.decode())) == runtime
    declarations = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )
    for spell, profile in PROFILES.items():
        assert declarations["spells"][spell]["definition"] == profile
    validate_profiles(PROFILES)
    assert runtime["tools"]["refresh_srd_appearance_sources"]["effects"][0]["condition"] == (
        "lasting_state.has_appearance_spells == true"
    )


def test_script_actual_ritual_time_no_slot_consumed_ink_and_native_alternative_handwriting():
    state, _, _ = _cast(
        "illusory_script",
        casting_mode="ritual",
        slot_resource="ritual",
        spellbook_readable=True,
        script_apparent="The door stays locked",
        script_language="common",
        script_handwriting="Angular printed letters",
    )
    source = next(iter(state[f"{SRD}.state"]["appearance_spell_sources"].values()))
    assert source["started_tick"] == 220
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 3
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2
    state[f"{SRD}.entities"]["guard"]["languages"] = ["common"]
    shown = _summary(
        _run("read_srd_illusory_script", state, observer_state="guard", subject="paper")
    )
    assert shown["Message"] == "The door stays locked"
    assert shown["Handwriting"] == "Angular printed letters"
    original = _summary(
        _run("read_srd_illusory_script", state, observer_state="other", subject="paper")
    )
    assert original["Message"] == "The door opens at dawn" and original["Handwriting"] == "hero"


def test_terrain_rejects_actual_solid_casting_path_after_real_long_cast_without_committing():
    state, data, parameters = _case(
        "hallucinatory_terrain", appearance_position=[20, 0, 0], appearance_cube_min=[20, -75, 0]
    )
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
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
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1)]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Cube.*origin"):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_independent_disguise_recast_and_exact_no_action_dismissal_restore_prior_source():
    state, data, parameters = _cast("disguise_self")
    first = next(iter(state[f"{SRD}.state"]["appearance_spell_sources"].values()))
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    parameters["appearance_specs"] = [_spec(description="A tall traveler", height_offset_inches=12)]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    records = list(state[f"{SRD}.state"]["appearance_spell_sources"].values())
    assert len(records) == 2 and all(r["installed"] for r in records)
    assert (
        _summary(_run("observe_srd_appearance", state, observer_state="other", subject="hero"))[
            "Appearance"
        ]
        == "A tall traveler"
    )
    _run(
        "dismiss_srd_appearance_spell", state, caster_state="hero", appearance_id=records[-1]["id"]
    )
    assert state[f"{SRD}.state"]["appearance_spell_sources"][first["id"]]["installed"]
    assert (
        _summary(_run("observe_srd_appearance", state, observer_state="other", subject="hero"))[
            "Appearance"
        ]
        == "A shorter traveler with a feathered hat"
    )


def test_actual_arcane_eye_later_subject_delivery_consumes_independent_seeming_appearance():
    state, data, parameters = _cast("seeming", appearance_specs=[_spec("guard")])
    eye = _grant_other_spell(state, data, "arcane_eye")
    _run("cast_spell_outside_combat", state, participant_data=data, **eye)
    information = _summary(
        _run("query_srd_information_spell", state, caster_state="hero", spell="arcane_eye")
    )["Information"]
    assert any(
        "guard: apparent A shorter traveler with a feathered hat" in row for row in information
    )


@pytest.mark.parametrize("save_modifier,affected", [(-100, True), (100, False)])
def test_seeming_unwilling_actual_charisma_save_and_willing_independent_target(
    save_modifier,
    affected,
    monkeypatch,
):
    monkeypatch.setattr(dice, "_rng", Random(3))
    state, data, parameters = _case(
        "seeming",
        appearance_specs=[
            _spec("guard", willing=False),
            _spec("other"),
        ],
    )
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["charisma"] = save_modifier
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    records = list(state[f"{SRD}.state"]["appearance_spell_sources"].values())
    assert ("guard" in {r["subject"] for r in records}) is affected
    assert "other" in {r["subject"] for r in records}
    state[f"{SRD}.entities"]["hero"]["is_alive"] = False
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert all(r["installed"] for r in state[f"{SRD}.state"]["appearance_spell_sources"].values())
    assert _summary(_run("observe_srd_appearance", state, observer_state="guard", subject="other"))[
        "Active illusion"
    ]


def test_native_study_actual_roll_recognizes_only_the_observer_and_costs_combat_action(monkeypatch):
    state, _, _ = _cast("disguise_self")
    monkeypatch.setattr(dice, "_rng", Random(7))
    state[f"{SRD}.entities"]["guard"]["skill_check_modifiers"] = {"investigation": 100}
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True, turn_active=True, active_actor="guard", action_available=True
    )
    result = _summary(
        _run("study_srd_appearance_creature", state, observer_state="guard", subject="hero")
    )
    assert result["Recognized"] and result["Total"] > 100
    assert not state[f"{SRD}.combat"]["action_available"]
    assert _summary(_run("observe_srd_appearance", state, observer_state="guard", subject="hero"))[
        "Known illusion"
    ]
    assert not _summary(
        _run("observe_srd_appearance", state, observer_state="other", subject="hero")
    )["Known illusion"]


def test_terrain_actual_cube_sample_exclusions_and_touch_show_real_terrain_under_vague_image():
    state, _, _ = _cast("hallucinatory_terrain", appearance_cube_min=[-75, -75, 0])
    physical = deepcopy(state[f"{SRD}.state"]["spatial_scene"])
    assert _summary(
        _run(
            "observe_srd_appearance",
            state,
            observer_state="other",
            subject="field",
            point=[75, 0, 0],
            use_point=True,
        )
    )["Active illusion"]
    assert not _summary(
        _run(
            "observe_srd_appearance",
            state,
            observer_state="other",
            subject="field",
            point=[76, 0, 0],
            use_point=True,
        )
    )["Active illusion"]
    assert not _summary(
        _run("observe_srd_appearance", state, observer_state="other", subject="paper")
    )["Active illusion"]
    assert not _summary(
        _run("observe_srd_appearance", state, observer_state="other", subject="guard")
    )["Active illusion"]
    _run("inspect_srd_appearance", state, observer_state="other", subject="field")
    observed = _summary(
        _run("observe_srd_appearance", state, observer_state="other", subject="field")
    )
    assert observed["Vague superimposed image"] and observed["Real terrain"] == "flat meadow"
    assert state[f"{SRD}.state"]["spatial_scene"] == physical


def test_current_truesight_recognizes_disguise_but_does_not_permanently_decode_script():
    state, _, _ = _cast("disguise_self")
    guard = state[f"{SRD}.entities"]["guard"]
    guard["truesight_feet"] = 120
    _run("observe_srd_appearance", state, observer_state="guard", subject="hero")
    guard["truesight_feet"] = 0
    assert _summary(_run("observe_srd_appearance", state, observer_state="guard", subject="hero"))[
        "Known illusion"
    ]
    state, _, _ = _cast("illusory_script")
    state[f"{SRD}.entities"]["guard"]["truesight_feet"] = 120
    assert _summary(
        _run("read_srd_illusory_script", state, observer_state="guard", subject="paper")
    )["Readable"]
    state[f"{SRD}.entities"]["guard"]["truesight_feet"] = 0
    assert not _summary(
        _run("read_srd_illusory_script", state, observer_state="guard", subject="paper")
    )["Readable"]


def _prepare_reader(state):
    supplied, _, params = _case("comprehend_languages")
    materials = supplied[f"{SRD}.entities"]["hero"]["spell_materials"]
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("reader", [5, 5, 0], participant_ref="reader", creature_type="humanoid")
    )
    _run(
        "prepare_srd_creature",
        state,
        creature_id="reader",
        name="Reader",
        source="Synthetic reader, common language, Wisdom +3, spell DC 15.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "comprehend_languages", "uses_per_day": 1}],
        inventory=[item["inventory_item"] for item in materials],
        spell_materials=materials,
    )
    params.pop("caster")
    params.pop("slot_resource")
    params.update(caster_state="reader", target="reader", targets=["reader"])
    _run("cast_spell_outside_combat", state, **params)


@pytest.mark.parametrize("apparent,understood", [("", False), ("The door stays locked", True)])
def test_comprehend_actual_minute_reads_only_shown_script_never_hidden_original(
    apparent, understood
):
    state, _, _ = _cast(
        "illusory_script", script_apparent=apparent, script_language="common" if apparent else ""
    )
    _prepare_reader(state)
    before = state[f"{SRD}.state"]["lasting_tick"]
    result = _summary(
        _run(
            "comprehend_srd_language",
            state,
            actor_state="reader",
            channel="written",
            perceived=True,
            touching_surface=True,
            surface_id="paper",
        )
    )
    assert state[f"{SRD}.state"]["lasting_tick"] == before + 20
    assert result["Literal meaning understood"] is understood
    assert result["Shown written message"] == apparent


def test_script_real_material_consumption_expiry_original_and_moved_invisible_holder():
    state, data, parameters = _case("illusory_script")
    materials = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    assert materials[0]["value_cp"] >= 1000
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert (
        state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"]
        == materials[0]["quantity"] - 1
    )
    paper = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "paper"
    )
    paper["metadata"].update(carried_by="hero")
    state[f"{SRD}.entities"]["hero"]["conditions"].append("invisible")
    with pytest.raises(RulesetPreconditionRejected, match="visible"):
        _run("read_srd_illusory_script", state, observer_state="other", subject="paper")
    state[f"{SRD}.entities"]["hero"]["conditions"].remove("invisible")
    _run("advance_srd_lasting_time", state, elapsed_rounds=100000)
    _run("advance_srd_lasting_time", state, elapsed_rounds=44000)
    assert (
        _summary(_run("read_srd_illusory_script", state, observer_state="other", subject="paper"))[
            "Message"
        ]
        == "The door opens at dawn"
    )
