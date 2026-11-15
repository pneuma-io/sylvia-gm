"""Adopt actual ongoing cantrip clauses without reclassifying retained consequences."""

from copy import deepcopy

import pytest
import yaml
from scripts.srd_full.srd_spell_removals import extend_runtime
from scripts.srd_full.srd_spell_source_instances import extend_runtime as extend_sources

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_illusion_removals import _compiled_owner as _compiled_owner
from tests.srd_full.test_srd_illusion_removals import _prepare
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case


def _fresh_case(spell="prestidigitation", mode="magic_mark"):
    state, data, params = _information_case("mind_blank")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    materials = _prepare(state, data, spell)
    if spell == "thaumaturgy":
        data["classes"][0]["id"] = "cleric"
        data["spellcasting_grants"][0].update(source="cleric", ability="wisdom")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(
        {
            "id": "small_object",
            "map_id": "information",
            "position": [3, 0, 0],
            "footprint": {"kind": "box", "half_extents": [0.25, 0.25]},
            "height": 0.5,
            "facing": [1, 0],
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "metadata": {"is_object": True, "plane": "material", "lighting": "bright"},
        }
    )
    budget._run(
        "record_srd_minor_world_subject",
        state,
        object_id="small_object",
        kind="water",
        nonliving=True,
    )
    params.update(
        spell=spell,
        slot_resource="cantrip",
        target="hero",
        material_item_ids=materials,
        minor_mode=mode,
        minor_object_id="small_object",
        minor_point=[3, 0, 0],
        minor_description="A harmless blue symbol",
        minor_operation="chill",
    )
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _effects(state):
    return [
        e for e in state[f"{SRD}.state"].get("turn_effects", []) if e["kind"] == "minor_world_spell"
    ]


def _case(spell="prestidigitation", mode="magic_mark"):
    state, data, params = _fresh_case(spell, mode)
    result = _cast(state, data, params)
    source = _effects(state)[-1]
    return state, data, params, source, result


@pytest.mark.parametrize(
    "spell,mode",
    [
        ("prestidigitation", "magic_mark"),
        ("prestidigitation", "minor_sensation"),
        ("thaumaturgy", "altered_eyes"),
        ("thaumaturgy", "booming_voice"),
        ("thaumaturgy", "tremors"),
    ],
)
def test_actual_ongoing_mode_gets_one_committed_cast_identity_and_exact_dismissal(spell, mode):
    state, _, _, source, result = _case(spell, mode)
    assert source["cast_id"] == "srd_cast:1" and source["source_caster_id"] == "hero"
    assert source["ongoing_duration"] is True and source["dispel_ongoing"] is True
    assert source["spell_level"] == 0
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=source["cast_id"])
    assert not _effects(state)
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert result.result["status"] == "committed"


@pytest.mark.parametrize(
    "spell,mode",
    [
        ("prestidigitation", "sensory_effect"),
        ("thaumaturgy", "phantom_sound"),
        ("elementalism", "sculpt_element"),
    ],
)
def test_retained_instantaneous_consequence_never_allocates_duration_cast_or_dispels(spell, mode):
    state, data, params = _fresh_case(spell, mode)
    result = _cast(state, data, params)
    effects = _effects(state)
    if effects:
        source = effects[-1]
        assert source["cast_id"] == "" and source["ongoing_duration"] is False
        assert source["dispel_ongoing"] is False
        source_id = source["source_id"]
    else:
        source_id = "no-ongoing-source"
    assert state[f"{SRD}.state"].get("spell_source_serial") is None
    assert result.result["context"]["_spell_source_cast_id"] == ""
    _prepare(state, data, "dispel_magic")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ongoing"):
        _cast(
            state,
            data,
            params
            | {
                "spell": "dispel_magic",
                "slot_resource": "spell_slot_3",
                "material_item_ids": [],
                "recovery_choice": "minor_world",
                "recovery_source_id": source_id,
            },
        )
    assert state == before


def test_two_same_tick_actual_casts_dismiss_and_dispel_only_selected_effect():
    state, data, params, first, _ = _case()
    _cast(state, data, params | {"minor_color": "green"})
    second = _effects(state)[-1]
    assert first["started_tick"] == second["started_tick"]
    assert first["cast_id"] != second["cast_id"]
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=first["cast_id"])
    assert {e["source_id"] for e in _effects(state)} == {second["source_id"]}
    _prepare(state, data, "dispel_magic")
    _cast(
        state,
        data,
        params
        | {
            "spell": "dispel_magic",
            "slot_resource": "spell_slot_3",
            "material_item_ids": [],
            "recovery_choice": "minor_world",
            "recovery_source_id": second["source_id"],
        },
    )
    assert not _effects(state)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


def test_actual_creature_dispel_ends_booming_voice_but_preserves_independent_scenery():
    state, data, params, voice, _ = _case("thaumaturgy", "booming_voice")
    params.update(minor_mode="tremors")
    _cast(state, data, params)
    scenery = _effects(state)[-1]
    _prepare(state, data, "dispel_magic")
    _cast(
        state,
        data,
        params
        | {"spell": "dispel_magic", "slot_resource": "spell_slot_3", "material_item_ids": []},
    )
    assert {e["source_id"] for e in _effects(state)} == {scenery["source_id"]}
    assert voice["source_id"] != scenery["source_id"]


def test_creature_dispel_does_not_erase_bookkeeping_scenery_and_moved_object_controls_range():
    state, data, params, source, _ = _case()
    _prepare(state, data, "dispel_magic")
    _cast(
        state,
        data,
        params
        | {"spell": "dispel_magic", "slot_resource": "spell_slot_3", "material_item_ids": []},
    )
    assert _effects(state)[0]["source_id"] == source["source_id"]
    actual = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "small_object"
    )
    actual["position"] = [150, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="range"):
        _cast(
            state,
            data,
            params
            | {
                "spell": "dispel_magic",
                "slot_resource": "spell_slot_3",
                "material_item_ids": [],
                "recovery_choice": "minor_world",
                "recovery_source_id": source["source_id"],
                "target_distance": 0,
            },
        )
    assert state == before


@pytest.mark.parametrize("removal", ["dismiss", "dispel"])
def test_next_caster_turn_creation_uses_exact_owner_for_source_and_actual_output(removal):
    state, data, params = _fresh_case(mode="minor_creation")
    budget._run(
        "record_srd_hand_sized_form",
        state,
        caster_state="hero",
        form_id="top",
        fiction="A small spinning top",
        kind="trinket",
        extent=[0.1, 0.1, 0.1],
        fits_hand=True,
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
    )
    params.update(minor_form_id="top", minor_output_id="top")
    budget._run("cast_spell", state, participant_data=data, **params)
    source = _effects(state)[-1]
    assert source["expires_tick"] is None and source["ongoing_duration"] is True
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    if removal == "dismiss":
        budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=source["cast_id"])
    else:
        _prepare(state, data, "dispel_magic")
        _cast(
            state,
            data,
            params
            | {
                "spell": "dispel_magic",
                "slot_resource": "spell_slot_3",
                "material_item_ids": [],
                "recovery_choice": "minor_world",
                "recovery_source_id": source["source_id"],
            },
        )
    assert not _effects(state)
    top = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "top")
    assert top["active"] is False


def test_shared_source_and_minor_removal_emitters_remain_stable_on_rebuild():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = extend_runtime(extend_sources(text))
    second = extend_runtime(extend_sources(first))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def test_public_mark_cast_reopen_exact_dismissal_and_retry_preserve_independent_cast(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "_spell_case", lambda spell: _fresh_case(spell))
    database = tmp_path / "minor-source.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "prestidigitation")
        first_receipt, first_command = _act(owner, "cast_spell_outside_combat", **params)
        assert first_receipt.status is ActionStatus.COMMITTED, first_receipt.result
        first_saved = owner.snapshot("casting")
        first = next(
            e
            for e in first_saved.state[f"{SRD}.state"]["turn_effects"]
            if e["kind"] == "minor_world_spell"
        )
        second_receipt, second_command = _act(
            owner, "cast_spell_outside_combat", **(params | {"minor_color": "green"})
        )
        assert second_receipt.status is ActionStatus.COMMITTED, second_receipt.result
        saved = owner.snapshot("casting")
        actual = [
            e
            for e in saved.state[f"{SRD}.state"]["turn_effects"]
            if e["kind"] == "minor_world_spell"
        ]
        assert len(actual) == 2 and actual[0]["cast_id"] != actual[1]["cast_id"]
        assert owner.execute_action(second_command) == second_receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        receipt, command = _act(
            owner, "dismiss_srd_spell", caster_state="hero", cast_id=first["cast_id"]
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        settled = owner.snapshot("casting")
        remaining = [
            e
            for e in settled.state[f"{SRD}.state"]["turn_effects"]
            if e["kind"] == "minor_world_spell"
        ]
        assert len(remaining) == 1 and remaining[0]["cast_id"] == actual[1]["cast_id"]
        assert (
            settled.state[f"{SRD}.entities"]["hero"]["resources"]
            == saved.state[f"{SRD}.entities"]["hero"]["resources"]
        )
        assert owner.execute_action(command) == receipt
        assert owner.execute_action(first_command) == first_receipt
        assert owner.snapshot("casting").state == settled.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == settled.state
