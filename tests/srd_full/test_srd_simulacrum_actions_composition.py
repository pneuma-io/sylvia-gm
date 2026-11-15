"""Actual copied actions preserve identity, budgets and durable command receipts."""

from copy import deepcopy
from dataclasses import replace

import pytest
from scripts.srd_full.srd_simulacrum_turns import TURN_FIELDS
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.srd_full import test_srd_public_spell_casting as public
from tests.srd_full import test_srd_simulacrum_actions as actions
from tests.srd_full.actions1_fixture import qualified_runtime
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act

SRD = actions.SRD
TIMELINE_FIELDS = (
    "order",
    "group_order",
    "round",
    "order_first",
    "order_second",
    "turn_sequence",
    "turn_index",
    "_round_advanced",
)


@pytest.fixture(autouse=True)
def _composed(monkeypatch):
    """Require this ticket's real final composition, including every generated output."""
    yield from qualified_runtime(monkeypatch)


def _run(case, tool, **params):
    state, caster, _params, original_data = case
    return actions.pc._run(state, caster, original_data, tool, **params)


def _copy_turn(case):
    state, caster, params, original_data = case
    actions.pc._cast(state, caster, params, original_data)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    _run(case, "begin_combat_turn")
    _run(
        case,
        "begin_srd_simulacrum_turn",
        caster_state="hero",
        duplicate_state="snow",
        command="Dodge",
    )


def _reject(case, tool, **params):
    before = deepcopy(case[0])
    with pytest.raises(RulesetPreconditionRejected):
        _run(case, tool, **params)
    assert case[0] == before


def test_copy_surge_spare_charge_rejects_same_turn_and_unused_grant_ends_with_subphase():
    case = actions._fighter_case()
    state = case[0]
    case[3].update(level=17, classes=[{"id": "fighter", "level": 17}])
    original = state[f"{SRD}.entities"]["guard"]
    original.update(hp=100, max_hp=100)
    original["resources"]["action_surge"] = 2
    original["resource_maxima"].update(action_surge=2, hit_die=17)
    _copy_turn(case)
    _run(case, "use_srd_simulacrum_action_surge", actor_state="snow")
    assert state[f"{SRD}.entities"]["snow"]["resources"]["action_surge"] == 1
    assert state[f"{SRD}.combat"]["action_surge_available"] is True
    _reject(case, "use_srd_simulacrum_action_surge", actor_state="snow")
    _run(case, "end_srd_simulacrum_turn", caster_state="hero", duplicate_state="snow")
    assert state[f"{SRD}.combat"].get("action_surge_available") is False
    assert state[f"{SRD}.entities"]["guard"]["resources"]["action_surge"] == 2
    _reject(
        case,
        "begin_srd_simulacrum_turn",
        caster_state="hero",
        duplicate_state="snow",
        command="Again",
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "wizard",
        "fighter_one",
        "dead",
        "incapacitated",
        "off_turn",
        "original_alias",
        "wrong_source",
        "retired",
        "pending_window",
    ],
)
def test_ineligible_copied_surge_rejects_without_spending_or_granting_action(invalid):
    case = actions._fighter_case()
    state, _caster, _params, original_data = case
    if invalid in {"wizard", "fighter_one"}:
        original_data.update(
            classes=[{"id": "wizard" if invalid == "wizard" else "fighter", "level": 1}], level=1
        )
    _copy_turn(case)
    duplicate = state[f"{SRD}.entities"]["snow"]
    actor = "snow"
    if invalid == "dead":
        duplicate["hp"] = 0
    elif invalid == "incapacitated":
        duplicate["conditions"] = ["incapacitated"]
    elif invalid == "off_turn":
        state[f"{SRD}.combat"]["active_actor"] = "hero"
    elif invalid == "original_alias":
        actor = "guard"
    elif invalid == "wrong_source":
        duplicate["simulacrum_character_original"] = "hero"
    elif invalid == "retired":
        state[f"{SRD}.state"]["created_actor_records"]["snow"]["active"] = False
    elif invalid == "pending_window":
        state[f"{SRD}.state"]["pending_srd_action_stack"] = [{"id": "pending-actions1"}]
    _reject(case, "use_srd_simulacrum_action_surge", actor_state=actor)


@pytest.mark.parametrize(
    "invalid", ["speed_zero", "incapacitated", "off_turn", "original_alias", "pending_window"]
)
def test_copied_dodge_keeps_native_body_action_and_pending_window_guards(invalid):
    case = actions._fighter_case()
    _copy_turn(case)
    state = case[0]
    duplicate = state[f"{SRD}.entities"]["snow"]
    actor = "snow"
    if invalid == "speed_zero":
        duplicate["speed"] = 0
    elif invalid == "incapacitated":
        duplicate["conditions"] = ["incapacitated"]
    elif invalid == "off_turn":
        state[f"{SRD}.combat"]["active_actor"] = "hero"
    elif invalid == "original_alias":
        actor = "guard"
    else:
        state[f"{SRD}.state"]["pending_srd_action_stack"] = [{"id": "pending-actions1"}]
    _reject(case, "take_srd_simulacrum_dodge_action", actor_state=actor)


def _public_case(monkeypatch):
    """Admit the original as a real second Character File, never the copied body."""
    holder = {}

    def selected_case(_spell):
        state, caster, params, original = actions._fighter_case()
        original.update(
            species="human", background="sage", size_rank=1, origin_feats=["magic_initiate"]
        )
        holder["original"] = original
        return state, caster, params

    create = AuthorityOwner.create_campaign

    def with_original(owner, request):
        original = replace(request.participants[0], participant_id="guard", data=holder["original"])
        return create(owner, replace(request, participants=request.participants + (original,)))

    monkeypatch.setattr(public, "_spell_case", selected_case)
    monkeypatch.setattr(AuthorityOwner, "create_campaign", with_original)


def _commit(owner, tool, **params):
    receipt, command = _act(owner, tool, **params)
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    return receipt, command, owner.snapshot("casting")


def _replay(database, command, receipt, saved):
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved


def _timeline(snapshot):
    combat = snapshot.state[f"{SRD}.combat"]
    assert combat["order"] and combat["group_order"] is True and "turn_index" in combat
    return {field: deepcopy(combat.get(field)) for field in TIMELINE_FIELDS} | {
        "lasting_tick": snapshot.state[f"{SRD}.state"]["lasting_tick"]
    }


def test_public_paid_fighter_copy_actions_reopen_retry_restore_creator_and_initiative(
    tmp_path, monkeypatch
):
    _public_case(monkeypatch)
    database = tmp_path / "actions1-public.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "simulacrum", target_overrides={"hp": 12, "max_hp": 14, "temp_hp": 0}
        )
        initial = owner.snapshot("casting")
        begin, beginning, saved = _commit(
            owner,
            "begin_srd_spell_cast",
            **(_begin(params) | {"target": "guard", "target_character": "guard"}),
        )
    _replay(database, beginning, begin, saved)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        partial, partial_command, partial_saved = _commit(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=3600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert (
            partial_saved.state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"]
            == 3600
        )
    _replay(database, partial_command, partial, partial_saved)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        continued, continuation, _ = _commit(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=3600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        cast, casting, copied = _commit(owner, "cast_spell_outside_combat", **params)
        duplicate = copied.state[f"{SRD}.entities"]["snow"]
        original = copied.state[f"{SRD}.entities"]["guard"]
        assert duplicate["creature_type"] == "construct"
        assert duplicate["created_individual_id"] == "snow"
        assert duplicate["simulacrum_character_original"] == "guard"
        assert "participant_id" not in duplicate["simulacrum_character_data"]
        assert {p.participant_id for p in copied.participants} == {"hero", "guard"}
        assert duplicate["resources"]["action_surge"] == original["resources"]["action_surge"] == 1
        assert duplicate["inventory"] == []
        assert (
            copied.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"]
            == initial.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] - 1
        )
        material_id = params["material_item_ids"][0]
        before_lot = next(
            lot
            for lot in initial.state[f"{SRD}.entities"]["hero"]["spell_materials"]
            if lot["id"] == material_id
        )
        after_lot = next(
            lot
            for lot in copied.state[f"{SRD}.entities"]["hero"]["spell_materials"]
            if lot["id"] == material_id
        )
        assert (
            before_lot["value_cp"] >= 150000 and after_lot["quantity"] == before_lot["quantity"] - 1
        )
        assert owner.execute_action(continuation) == continued
        assert owner.snapshot("casting") == copied
    _replay(database, casting, cast, copied)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _commit(owner, "start_group_combat_encounter", combatants=["hero", "guard", "other"])
        _commit(owner, "begin_combat_turn")
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "hero"
        _commit(owner, "take_dodge_action", actor="hero", actor_state="hero")
        creator = owner.snapshot("casting")
        creator_entity = deepcopy(creator.state[f"{SRD}.entities"]["hero"])
        original_entity = deepcopy(creator.state[f"{SRD}.entities"]["guard"])
        timeline = _timeline(creator)
        for tool in ("take_srd_simulacrum_dodge_action", "use_srd_simulacrum_action_surge"):
            with pytest.raises(MechanicsRejected):
                _act(owner, tool, actor_state="guard")
            assert owner.snapshot("casting") == creator
        started, starting, during = _commit(
            owner,
            "begin_srd_simulacrum_turn",
            caster_state="hero",
            duplicate_state="snow",
            command="Dodge, Action Surge, Dodge",
        )
        assert _timeline(during) == timeline
    _replay(database, starting, started, during)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        dodge, dodging, first = _commit(
            owner, "take_srd_simulacrum_dodge_action", actor_state="snow"
        )
        assert first.state[f"{SRD}.entities"]["snow"]["dodging"] is True
        assert first.state[f"{SRD}.combat"]["action_available"] is False
    _replay(database, dodging, dodge, first)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        surge, surging, granted = _commit(
            owner, "use_srd_simulacrum_action_surge", actor_state="snow"
        )
        assert granted.state[f"{SRD}.entities"]["snow"]["resources"]["action_surge"] == 0
        assert granted.state[f"{SRD}.combat"]["action_surge_available"] is True
        assert granted.state[f"{SRD}.entities"]["guard"] == original_entity
    _replay(database, surging, surge, granted)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        second_dodge, second_command, spent = _commit(
            owner, "take_srd_simulacrum_dodge_action", actor_state="snow"
        )
        assert spent.state[f"{SRD}.combat"]["action_available"] is False
        assert spent.state[f"{SRD}.combat"]["action_surge_available"] is False
        with pytest.raises(MechanicsRejected):
            _act(owner, "use_srd_simulacrum_action_surge", actor_state="snow")
        assert owner.snapshot("casting") == spent
    _replay(database, second_command, second_dodge, spent)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        ended, ending, final = _commit(
            owner, "end_srd_simulacrum_turn", caster_state="hero", duplicate_state="snow"
        )
        for field in TURN_FIELDS:
            assert final.state[f"{SRD}.combat"].get(field) == creator.state[f"{SRD}.combat"].get(
                field
            ), field
        for field in (
            "bonus_action_available",
            "reaction_available",
            "resources",
            "spell_slot_cast_this_turn",
        ):
            assert final.state[f"{SRD}.entities"]["hero"].get(field) == creator_entity.get(field), (
                field
            )
        assert final.state[f"{SRD}.entities"]["guard"] == original_entity
        assert _timeline(final) == timeline
        assert {p.participant_id for p in final.participants} == {"hero", "guard"}
    _replay(database, ending, ended, final)


def test_genuine_nonfighter_copy_can_dodge_but_cannot_use_fighter_grant():
    case = actions.pc._case()
    _copy_turn(case)
    _run(case, "take_srd_simulacrum_dodge_action", actor_state="snow")
    assert case[0][f"{SRD}.entities"]["snow"]["dodging"] is True
    _reject(case, "use_srd_simulacrum_action_surge", actor_state="snow")


def test_copy_surge_extra_action_cannot_pay_for_magic():
    case = actions._fighter_case()
    _copy_turn(case)
    _run(case, "take_srd_simulacrum_dodge_action", actor_state="snow")
    _run(case, "use_srd_simulacrum_action_surge", actor_state="snow")
    _reject(
        case,
        "spend_spell_casting_budget",
        caster_state="snow",
        casting_time="action",
        slot_resource="cantrip",
    )
    assert case[0][f"{SRD}.combat"]["action_surge_available"] is True
    _run(case, "take_srd_simulacrum_dodge_action", actor_state="snow")
    assert case[0][f"{SRD}.combat"]["action_available"] is False
