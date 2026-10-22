"""PC originals retain genuine separate Construct capability and resource identity."""

from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_simulacrum as sim
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_wave3_information import _entity

SRD = sim.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from sim._compiled.__wrapped__(monkeypatch)


def _case():
    state, caster, params = sim._case()
    original_data = deepcopy(caster)
    original_data.update(
        name="Original Wizard",
        level=2,
        classes=[{"id": "wizard", "level": 2}],
        abilities={
            "strength": 10,
            "dexterity": 14,
            "constitution": 12,
            "intelligence": 18,
            "wisdom": 10,
            "charisma": 8,
        },
        saving_throw_proficiencies=["intelligence", "wisdom"],
        skill_proficiencies=["arcana"],
        spellcasting_grants=[
            {
                "source": "wizard",
                "ability": "intelligence",
                "spells": ["magic_missile", "fire_bolt"],
            }
        ],
        wizard_spellbook=["magic_missile"],
    )
    original = state[f"{SRD}.entities"]["guard"]
    original.update(
        creature_kind="character",
        creature_type="humanoid",
        hp=12,
        max_hp=14,
        temp_hp=0,
        inventory=[],
        exhaustion_level=0,
        conditions=[],
        armor_class=12,
        speed=30,
        size_rank=1,
        resources={"spell_slot_1": 2, "hit_die": 1},
        resource_maxima={"spell_slot_1": 3, "hit_die": 2},
        prepared_spells=["magic_missile", "fire_bolt"],
        saving_throw_modifiers={},
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(
        _entity("guard", [3, 0, 0], participant_ref="guard", creature_type="humanoid")
    )
    params.update(target="guard", target_character="guard")
    return state, caster, params, original_data


def _run(state, caster_data, original_data, tool, **params):
    return sim._run(tool, state, caster_data, host_overrides=original_data, **params)


def _cast(state, caster, params, original_data):
    _run(
        state,
        caster,
        original_data,
        "begin_srd_spell_cast",
        **(_begin(params) | {"target": "guard", "target_character": "guard"}),
    )
    _run(
        state,
        caster,
        original_data,
        "continue_srd_spell_cast",
        caster_state="hero",
        elapsed_rounds=7200,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return _run(state, caster, original_data, "cast_spell_outside_combat", **params)


def _missile(params, *, source):
    choices = {
        key: value
        for key, value in params.items()
        if key not in {"caster", "target_character", "simulacrum_pile", "simulacrum_friends"}
    }
    choices.update(
        spell="magic_missile",
        caster_state=source,
        target="beast",
        slot_resource="spell_slot_1",
        material_item_ids=[],
        target_distance=6,
        target_willing=False,
    )
    if source == "guard":
        choices["caster"] = "guard"
    return choices


def test_paid_pc_copy_spends_own_slots_and_uses_detached_native_check_facts():
    state, caster, params, original_data = _case()
    original = deepcopy(state[f"{SRD}.entities"]["guard"])
    _cast(state, caster, params, original_data)
    duplicate = state[f"{SRD}.entities"]["snow"]
    assert duplicate["creature_type"] == "construct"
    assert duplicate["max_hp"] == 7 and duplicate["hp"] == 7
    assert duplicate["resources"] == original["resources"]
    assert duplicate["ability_scores"] == original_data["abilities"]
    assert duplicate["saving_throw_modifiers"]["intelligence"] == 6
    assert "participant_id" not in duplicate["simulacrum_character_data"]
    assert duplicate["spell_uses_remaining"] is None

    _run(
        state,
        caster,
        original_data,
        "cast_spell_outside_combat",
        **_missile(params, source="guard"),
    )
    assert state[f"{SRD}.entities"]["guard"]["resources"]["spell_slot_1"] == 1
    assert state[f"{SRD}.entities"]["snow"]["resources"]["spell_slot_1"] == 2
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="hero")
    _run(state, caster, original_data, "begin_combat_turn")
    _run(state, caster, original_data, "take_dodge_action", actor="hero", actor_state="hero")
    creator_budget = deepcopy(state[f"{SRD}.combat"])
    tick = state[f"{SRD}.state"]["lasting_tick"]
    _run(
        state,
        caster,
        original_data,
        "begin_srd_simulacrum_turn",
        caster_state="hero",
        duplicate_state="snow",
        command="Cast Magic Missile on the Beast",
    )
    _run(state, caster, original_data, "cast_spell", **_missile(params, source="snow"))
    assert state[f"{SRD}.combat"]["action_available"] is False
    _run(
        state,
        caster,
        original_data,
        "end_srd_simulacrum_turn",
        caster_state="hero",
        duplicate_state="snow",
    )
    for field in (
        "active_actor",
        "current_actor",
        "action_available",
        "normal_action_spent",
        "movement_remaining",
        "movement_spent",
        "turn_sequence",
    ):
        assert state[f"{SRD}.combat"].get(field) == creator_budget.get(field)
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
    assert state[f"{SRD}.entities"]["snow"]["resources"]["spell_slot_1"] == 1
    assert state[f"{SRD}.entities"]["guard"]["resources"]["spell_slot_1"] == 1
    check = _run(
        state,
        caster,
        original_data,
        "check_srd_simulacrum_character_ability",
        actor_state="snow",
        ability="intelligence",
        skill="arcana",
        circumstantial_modifier=0,
        target_number=10,
    )
    assert check.result["context"]["_combined_modifier"] == 6
    changed_artifact = deepcopy(original_data)
    changed_artifact["abilities"]["intelligence"] = 10
    repeated = _run(
        state,
        caster,
        changed_artifact,
        "check_srd_simulacrum_character_ability",
        actor_state="snow",
        ability="intelligence",
        skill="arcana",
        circumstantial_modifier=0,
        target_number=10,
    )
    assert repeated.result["context"]["_combined_modifier"] == 6


def test_unmatched_character_and_original_participant_alias_reject_atomically():
    state, caster, params, original_data = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="matching validated"):
        _run(
            state,
            caster,
            original_data,
            "begin_srd_spell_cast",
            **(_begin(params) | {"target": "guard", "target_character": "hero"}),
        )
    assert state == before
    _cast(state, caster, params, original_data)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one identity"):
        _run(
            state,
            caster,
            original_data,
            "cast_spell_outside_combat",
            **(_missile(params, source="snow") | {"caster": "guard"}),
        )
    assert state == before


def test_public_paid_pc_original_reopens_for_independent_slot_cast_and_exact_retries(
    tmp_path, monkeypatch
):
    from scripts.srd_full.srd_simulacrum_consumers import PUBLIC_TOOLS as REPAIR_TOOLS
    from scripts.srd_full.srd_simulacrum_turns import PUBLIC_TOOLS as TURN_TOOLS

    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from sylvia_gm.domain.package_library import canonical_json_bytes
    from tests import test_srd_plant_growth as growth
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(growth, "PUBLIC_TOOLS", REPAIR_TOOLS | TURN_TOOLS)
    growth._public_fixture(monkeypatch)
    record = deepcopy(growth.bundled_packages._srd_drafts()[0].record)
    root = tmp_path / "admission"
    record_path = root / "rulesets/dnd-srd-5.2.1/package-record.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_bytes(canonical_json_bytes(record))
    monkeypatch.setattr(sim.jar.public_casting, "ROOT", root)
    original_holder = {}

    def public_case(*_args, **_kwargs):
        state, caster, params, original_data = _case()
        original_data.update(
            species="human", background="sage", size_rank=1, origin_feats=["magic_initiate"]
        )
        original_holder["data"] = original_data
        return state, caster, params

    # Admit the original as a real second Character File. The created Construct never
    # enters the participant collection and has no copied or fabricated participant ID.
    create = AuthorityOwner.create_campaign

    def with_original(owner, request):
        original = replace(
            request.participants[0], participant_id="guard", data=original_holder["data"]
        )
        return create(owner, replace(request, participants=request.participants + (original,)))

    monkeypatch.setattr(AuthorityOwner, "create_campaign", with_original)
    monkeypatch.setattr(sim.jar.public_casting, "_spell_case", public_case)
    database = tmp_path / "simulacrum-character.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = sim.jar.public_casting._create_spell_campaign(
            owner, "simulacrum", target_overrides={"hp": 12, "max_hp": 14, "temp_hp": 0}
        )
        began, beginning = _act(
            owner,
            "begin_srd_spell_cast",
            **(_begin(params) | {"target": "guard", "target_character": "guard"}),
        )
        assert began.status is ActionStatus.COMMITTED, began.result
        saved_begin = owner.snapshot("casting")
        assert owner.execute_action(beginning) == began
        assert owner.snapshot("casting") == saved_begin
    with AuthorityOwner.open(database, "casting-owner") as owner:
        continued, continuation = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=7200,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, casting = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        duplicate = saved.state[f"{SRD}.entities"]["snow"]
        assert duplicate["simulacrum_character_original"] == "guard"
        assert duplicate["resources"]["spell_slot_1"] == 2
        participant_ids = {participant.participant_id for participant in saved.participants}
        assert participant_ids == {"hero", "guard"}
        for action, receipt in ((continuation, continued), (casting, cast)):
            assert owner.execute_action(action) == receipt
            assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        missile, firing = _act(
            owner, "cast_spell_outside_combat", **_missile(params, source="snow")
        )
        assert missile.status is ActionStatus.COMMITTED, missile.result
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["snow"]["resources"]["spell_slot_1"] == 1
        assert after.state[f"{SRD}.entities"]["guard"]["resources"]["spell_slot_1"] == 2
        assert owner.execute_action(firing) == missile
        assert owner.snapshot("casting") == after
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == after
