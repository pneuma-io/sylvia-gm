"""Travel consumes actual geometry, movement and casting owners across later actions."""

from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest
from scripts.srd_full.srd_spell_travel import PROFILES, validate_profiles

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.spatial import SpatialEngine
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_wave3_information import SRD, _entity, _free, _scene, _wall
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case


def _placement(scene, origin, **changes):
    request = {
        "kind": "placement",
        "map_id": "information",
        "moving_entity_id": "hero",
        "origin": origin,
        "exclude_entity_ids": [],
        "metadata_filter": {"key": "plane", "value": "material", "missing_matches": True},
        "max_candidates": 10000,
    }
    request.update(changes)
    return SpatialEngine(scene).query(request).as_payload()


def test_placement_exact_union_nearest_and_deterministic_ties():
    scene = _scene(_entity("hero", [0, 0, 0]))
    scene["geometry"] = [
        _wall(10),
        {
            **_wall(10),
            "id": "cross",
            "volume": {"kind": "box", "min": [0, -2, -10], "max": [30, 2, 20]},
        },
    ]
    result = _placement(scene, [15, 0, 0])
    assert result["supported"] and result["found"]
    assert result["position"] == [20.4, -2.4, 0]
    assert result["distance"] == pytest.approx((5.4**2 + 2.4**2) ** 0.5)
    assert _placement(scene, [15, 0, 0]) == result


def test_placement_map_bounds_and_three_dimensional_shunt():
    scene = _scene(_entity("hero", [0, 0, 0]))
    scene["geometry"] = [
        {**_wall(10), "volume": {"kind": "box", "min": [-50, -50, -20], "max": [50, 50, 1]}}
    ]
    result = _placement(scene, [0, 0, 0])
    assert result["position"] == [0, 0, 1]
    assert result["distance"] == 1
    bounded = _placement(_scene(_entity("hero", [0, 0, 0])), [30000, 0, 0])
    assert bounded["position"] == [29999.6, 0, 0]


def test_placement_uses_actual_occupants_and_plane_metadata():
    scene = _scene(_entity("hero", [0, 0, 0]), _entity("other", [10, 0, 0], plane="ethereal"))
    assert _placement(scene, [10, 0, 0])["distance"] == 0
    query = {"metadata_filter": {"key": "plane", "value": "ethereal", "missing_matches": False}}
    assert _placement(scene, [10, 0, 0], **query)["distance"] == pytest.approx(0.8)
    assert _placement(scene, [10, 0, 0], exclude_entity_ids=["other"], **query)["distance"] == 0


def test_placement_never_claims_approximate_curves_or_incomplete_search():
    scene = _scene(_entity("hero", [0, 0, 0]), _entity("round", [12, 0, 0]))
    scene["entities"][1]["footprint"] = {"kind": "circle", "radius": 2}
    result = _placement(scene, [12, 0, 0])
    assert not result["supported"] and result["reason"] == "unsupported_geometry"
    scene["entities"].pop()
    scene["geometry"] = [_wall(4)]
    result = _placement(scene, [12, 0, 0], max_candidates=1)
    assert not result["supported"] and result["reason"] == "candidate_limit"
    assert result["position"] is None


def _case(spell, **changes):
    state, data, parameters = _information_case(spell)
    _free(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] += [_ambient_zone(plane) for plane in ("material", "ethereal", "prison")]
    scene["entities"][1]["position"] = [5, 0, 0]
    state[f"{SRD}.entities"]["hero"].update(size_rank=2, hp=500, max_hp=500)
    state[f"{SRD}.entities"]["guard"].update(hp=500, max_hp=500, temp_hp=0)
    parameters.update(travel_destination=[30, 0, 0], travel_companion_destination=[35, 0, 0])
    parameters.update(changes)
    return state, data, parameters


def _ambient_zone(plane, lighting="bright"):
    return {
        "id": "ambient_" + plane,
        "map_id": "information",
        "shape": {"kind": "box", "min": [-1000, -1000, -100], "max": [1000, 1000, 100]},
        "metadata": {"plane": plane, "lighting": lighting},
    }


def _cast(spell, **changes):
    state, data, params = _case(spell, **changes)
    if PROFILES[spell]["casting_time"] == "1_minute":
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
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
    result = _run("cast_spell", state, participant_data=data, **params)
    return state, data, params, result


def _point(state, subject="hero"):
    return next(
        entity["position"]
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == subject
    )


class _TravelDice:
    """Verify that each actual game roll consumes only its expected source die."""

    def __init__(self, rolls):
        self.rolls = list(rolls)
        self.consumed = []

    def randint(self, low, high):
        sides, value = self.rolls.pop(0)
        assert (low, high) == (1, sides)
        assert low <= value <= high
        self.consumed.append((sides, value))
        return value


def _teleport_case(monkeypatch, rolls, familiarity="very_familiar", **changes):
    actual_dice = _TravelDice(rolls)
    monkeypatch.setattr(dice, "_rng", actual_dice)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _case("teleport", travel_destination_subject="orb", **changes)
    anchor = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    anchor.update(position=[100, 0, 0], movement="passable", visibility="clear")
    _run(
        "record_srd_teleport_destination",
        state,
        caster_state="hero",
        destination_subject="orb",
        familiarity=familiarity,
    )
    return state, data, params, actual_dice


def _teleport_continue(state):
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    return _run(
        "continue_srd_teleport_resolution",
        state,
        caster_state="hero",
        resolution_id=record["id"],
    )


@pytest.mark.parametrize(
    "familiarity,roll,outcome",
    [
        ("very_familiar", 5, "mishap"),
        ("very_familiar", 6, "similar_area"),
        ("very_familiar", 13, "similar_area"),
        ("very_familiar", 14, "off_target"),
        ("very_familiar", 24, "off_target"),
        ("very_familiar", 25, "on_target"),
        ("seen_casually", 33, "mishap"),
        ("seen_casually", 34, "similar_area"),
        ("seen_casually", 44, "off_target"),
        ("seen_casually", 54, "on_target"),
        ("viewed_once", 43, "mishap"),
        ("viewed_once", 44, "similar_area"),
        ("described", 54, "off_target"),
        ("described", 74, "on_target"),
        ("false_destination", 50, "mishap"),
        ("false_destination", 51, "similar_area"),
    ],
)
def test_teleport_actual_percentile_uses_source_familiarity_table(
    monkeypatch,
    familiarity,
    roll,
    outcome,
):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, roll)], familiarity)
    _run("cast_spell", state, participant_data=data, **params)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["outcome"] == outcome
    assert record["paid"] and record["slot_level"] == 7
    assert record["history"][0]["roll"]["results"] == [roll]
    assert record["caster"] == "hero" and record["party"][0]["target"] == "hero"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert actual_dice.consumed == [(100, roll)]
    assert _point(state) == [0, 0, 0]


def test_teleport_known_permanent_sigil_has_actual_on_target_party_arrival(monkeypatch):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, 1)], targets=["guard"])
    hero = state[f"{SRD}.entities"]["hero"]
    hero.pop("teleport_destinations")
    hero["known_circle_sigils"] = ["amber"]
    anchor = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    anchor["metadata"].update(teleportation_circle=True, sigil="amber")
    _run("cast_spell", state, participant_data=data, **params)
    _teleport_continue(state)
    _teleport_continue(state)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["familiarity"] == "permanent_circle" and record["status"] == "completed"
    assert _point(state) == [100, 0, 0] and _point(state, "guard") == [105, 0, 0]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert actual_dice.consumed == [(100, 1)]


def test_teleport_repeated_mishaps_roll_each_traveler_then_one_party_reroll(monkeypatch):
    rolls = [(100, 1)] + [(10, 1)] * 3 + [(10, 2)] * 3 + [(100, 2)]
    rolls += [(10, 3)] * 3 + [(10, 4)] * 3 + [(100, 100)]
    state, data, params, actual_dice = _teleport_case(monkeypatch, rolls, targets=["guard"])
    _run("cast_spell", state, participant_data=data, **params)
    for _ in range(10):
        _teleport_continue(state)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["stage"] == "arrival"
    assert [h["kind"] for h in record["history"]] == [
        "percentile",
        "mishap_roll",
        "mishap",
        "mishap_roll",
        "mishap",
        "percentile",
        "mishap_roll",
        "mishap",
        "mishap_roll",
        "mishap",
        "percentile",
    ]
    assert [h["target"] for h in record["history"] if h["kind"] == "mishap"] == [
        "hero",
        "guard",
        "hero",
        "guard",
    ]
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 488
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 482
    assert _point(state) == [0, 0, 0] and _point(state, "guard") == [5, 0, 0]
    _teleport_continue(state)
    _teleport_continue(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="paid pending"):
        _teleport_continue(state)
    assert state == before and actual_dice.rolls == []
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2


@pytest.mark.parametrize(
    "invalid,message",
    [
        ("range", "range"),
        ("plane", "current plane"),
        ("unwilling", "willing"),
        ("unknown", "knowledge"),
        ("moved_anchor", "knowledge"),
        ("blind", "see"),
    ],
)
def test_teleport_preflight_rejects_actual_invalid_facts_before_payment_or_dice(
    monkeypatch,
    invalid,
    message,
):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [], targets=["guard"])
    hero = state[f"{SRD}.entities"]["hero"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if invalid == "range":
        next(e for e in scene["entities"] if e["id"] == "guard")["position"] = [11, 0, 0]
    elif invalid == "plane":
        next(e for e in scene["entities"] if e["id"] == "orb")["metadata"]["plane"] = "ethereal"
    elif invalid == "unwilling":
        params["all_targets_willing"] = False
    elif invalid == "unknown":
        hero.pop("teleport_destinations")
        params["targets"] = None
        next(zone for zone in scene["zones"] if zone["id"] == "ambient_material")["metadata"][
            "lighting"
        ] = "darkness"
    elif invalid == "moved_anchor":
        next(e for e in scene["entities"] if e["id"] == "orb")["position"] = [101, 0, 0]
        params["targets"] = None
        next(zone for zone in scene["zones"] if zone["id"] == "ambient_material")["metadata"][
            "lighting"
        ] = "darkness"
    else:
        hero["conditions"].append("blinded")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=message):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before and actual_dice.consumed == []


@pytest.mark.parametrize("direction,expected", [(1, [26500, 0, 0]), (7, [100, 26400, 0])])
def test_teleport_off_target_actual_miles_and_compass_direction(monkeypatch, direction, expected):
    state, data, params, actual_dice = _teleport_case(
        monkeypatch,
        [(100, 14), (12, 2), (12, 3), (8, direction)],
    )
    _run("cast_spell", state, participant_data=data, **params)
    _teleport_continue(state)
    _teleport_continue(state)
    assert _point(state) == expected
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["history"][1]["miles"]["results"] == [2, 3]
    assert record["history"][1]["direction"]["results"] == [direction]
    assert actual_dice.rolls == []


def test_teleport_similar_area_uses_closest_actual_qualified_anchor_and_no_reroll(monkeypatch):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, 6)])
    _run("cast_spell", state, participant_data=data, **params)
    _teleport_continue(state)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["stage"] == "similar_area" and record["waiting_reason"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for identity, point in (("far_lab", [300, 0, 0]), ("near_lab", [150, 0, 0])):
        anchor = _entity(identity, point, plane="material")
        anchor.update(movement="passable", visibility="clear")
        scene["entities"].append(anchor)
        _run(
            "record_srd_teleport_similar_area",
            state,
            destination_subject="orb",
            similar_subject=identity,
        )
        scene = state[f"{SRD}.state"]["spatial_scene"]
    _teleport_continue(state)
    _teleport_continue(state)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["similar_anchor"] == "near_lab" and record["similar_distance"] == 50
    assert _point(state) == [150, 0, 0] and actual_dice.consumed == [(100, 6)]


def test_teleport_occupied_exact_arrival_retains_paid_outcome_without_inventing_shunt(monkeypatch):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, 100)])
    blocker = _entity("occupant", [100, 0, 0], plane="material")
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(blocker)
    _run("cast_spell", state, participant_data=data, **params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="exact arrival is occupied"):
        _teleport_continue(state)
    assert state == before and actual_dice.consumed == [(100, 100)]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2


def test_teleport_maximum_party_uses_actual_sight_and_finite_single_recipient_steps(monkeypatch):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, 100)])
    positions = [(5, 0), (4, 4), (0, 5), (-4, 4), (-5, 0), (-4, -4), (0, -5), (4, -4)]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    targets = []
    for index, (x, y) in enumerate(positions):
        identity = f"traveler_{index}"
        actor = deepcopy(state[f"{SRD}.entities"]["guard"])
        actor["name"] = identity
        state[f"{SRD}.entities"][identity] = actor
        entity = _entity(identity, [x, y, 0], plane="material", participant_ref=identity)
        scene["entities"].append(entity)
        targets.append(identity)
    params["targets"] = targets
    _run("cast_spell", state, participant_data=data, **params)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert len(record["party"]) == 9
    pending = record["reaction_action_id"]
    lasting = state[f"{SRD}.state"]
    assert pending is not None and lasting["pending_srd_action_stack"] == [pending]
    assert lasting["pending_srd_actions"][pending]["kind"] == "teleport_resolution"
    assert lasting["pending_srd_actions"][pending]["actor_state_id"] == "hero"
    for _ in range(9):
        _teleport_continue(state)
    assert _point(state) == [100, 0, 0]
    for identity, (x, y) in zip(targets, positions, strict=True):
        assert _point(state, identity) == [100 + x, y, 0]
    assert state[f"{SRD}.entities"]["hero"]["teleport_resolution"]["status"] == "completed"
    lasting = state[f"{SRD}.state"]
    assert lasting["pending_srd_action_stack"] == []
    assert lasting["pending_srd_actions"] == {}
    assert actual_dice.consumed == [(100, 100)]


def test_teleport_pending_cast_rejects_wrong_identity_and_second_payment(monkeypatch):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, 100)])
    _run("cast_spell", state, participant_data=data, **params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="exact saved Teleport identity"):
        _run("continue_srd_teleport_resolution", state, caster_state="hero", resolution_id=2)
    with pytest.raises(RulesetPreconditionRejected, match="paid Teleport journey"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before and actual_dice.consumed == [(100, 100)]


def test_teleport_mishap_incapacitation_keeps_the_paid_resolution(monkeypatch):
    state, data, params, actual_dice = _teleport_case(
        monkeypatch,
        [(100, 1), (10, 1), (10, 1), (10, 1), (100, 100)],
    )
    state[f"{SRD}.entities"]["hero"]["hp"] = 1
    _run("cast_spell", state, participant_data=data, **params)
    _teleport_continue(state)
    _teleport_continue(state)
    assert "unconscious" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _teleport_continue(state)
    _teleport_continue(state)
    assert _point(state) == [100, 0, 0] and actual_dice.rolls == []


def test_teleport_lethal_caster_mishap_still_settles_companion_damage_and_party_arrival(
    monkeypatch,
):
    state, data, params, actual_dice = _teleport_case(
        monkeypatch,
        [(100, 1), (10, 10), (10, 10), (10, 10), (10, 3), (10, 3), (10, 3), (100, 100)],
        targets=["guard"],
    )
    state[f"{SRD}.entities"]["hero"].update(hp=1, max_hp=1, temp_hp=0)
    state[f"{SRD}.entities"]["guard"]["temp_hp"] = 0
    _run("cast_spell", state, participant_data=data, **params)
    _teleport_continue(state)
    _teleport_continue(state)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is False and "dead" in hero["conditions"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 500
    for _ in range(5):
        _teleport_continue(state)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is False and hero["resources"]["spell_slot_7"] == 2
    assert hero["teleport_resolution"]["status"] == "completed"
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 491
    assert _point(state) == [100, 0, 0] and _point(state, "guard") == [105, 0, 0]
    assert actual_dice.rolls == []


def test_teleport_saved_mishap_roll_survives_rejected_continuation(monkeypatch):
    state, data, params, actual_dice = _teleport_case(
        monkeypatch,
        [(100, 1), (10, 2), (10, 3), (10, 4)],
    )
    _run("cast_spell", state, participant_data=data, **params)
    _teleport_continue(state)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["stage"] == "mishap_damage"
    assert record["pending_mishap"]["roll"]["results"] == [2, 3, 4]
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 500
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="exact saved Teleport identity"):
        _run("continue_srd_teleport_resolution", state, caster_state="hero", resolution_id=2)
    assert state == before and actual_dice.rolls == []
    _teleport_continue(state)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 491
    assert actual_dice.consumed == [(100, 1), (10, 2), (10, 3), (10, 4)]


def test_teleport_current_actual_sight_qualifies_very_familiar_without_history(monkeypatch):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, 25)])
    state[f"{SRD}.entities"]["hero"].pop("teleport_destinations")
    _run("cast_spell", state, participant_data=data, **params)
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    assert record["familiarity"] == "very_familiar" and record["outcome"] == "on_target"
    _teleport_continue(state)
    assert _point(state) == [100, 0, 0] and actual_dice.consumed == [(100, 25)]


def test_public_teleport_paid_roll_damage_reopen_continuation_and_retry(monkeypatch, tmp_path):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, params, actual_dice = _teleport_case(
        monkeypatch,
        [(100, 1), (10, 2), (10, 3), (10, 4), (100, 100)],
    )
    state[f"{SRD}.entities"]["hero"].pop("teleport_destinations")
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / "teleport.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "teleport")
        mechanics = StatelessMcpMechanics(owner)
        knowledge = ActionCommand(
            campaign_id="casting",
            action_id="destination",
            idempotency_key="destination",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_teleport_destination",
            parameters={
                "caster_state": "hero",
                "destination_subject": "orb",
                "familiarity": "described",
            },
        )
        assert mechanics.execute_action(knowledge).status is ActionStatus.COMMITTED
        cast = replace(
            knowledge,
            action_id="paid-teleport",
            idempotency_key="paid-teleport",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        record = saved.state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
        assert record["paid"] and record["stage"] == "mishap"
        assert record["familiarity"] == "described"
        assert mechanics.execute_action(cast) == receipt
        assert owner.snapshot("casting").state == saved.state
        assert actual_dice.consumed == [(100, 1)]
        continuation = replace(
            cast,
            tool_name="continue_srd_teleport_resolution",
            parameters={"caster_state": "hero", "resolution_id": record["id"]},
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        roll = replace(
            continuation,
            action_id="mishap-roll",
            idempotency_key="mishap-roll",
            expected_revision=saved.revision,
        )
        receipt = mechanics.execute_action(roll)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        record = saved.state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
        assert record["stage"] == "mishap_damage"
        assert record["pending_mishap"]["roll"]["results"] == [2, 3, 4]
        assert saved.state[f"{SRD}.entities"]["hero"]["hp"] == 500
        assert mechanics.execute_action(roll) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        for stage in ("mishap-damage", "percentile-reroll", "actual-arrival"):
            command = replace(
                continuation,
                action_id=stage,
                idempotency_key=stage,
                expected_revision=owner.snapshot("casting").revision,
            )
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED
            saved = owner.snapshot("casting")
            assert mechanics.execute_action(command) == receipt
            assert owner.snapshot("casting").state == saved.state
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == 491 and hero["resources"]["spell_slot_7"] == 2
        assert hero["teleport_resolution"]["status"] == "completed"
        assert _point(saved.state) == [100, 0, 0] and actual_dice.rolls == []


def _progressive_travel_case(monkeypatch, spell, *, mode=None):
    """Install the restriction by a genuine named cast before supplying later travel inputs."""
    source_spell = "imprisonment" if mode else "divine_word"
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _case(source_spell, target="hero", targets=["hero"])
    hero = state[f"{SRD}.entities"]["hero"]
    hero["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], -100)
    if mode:
        material = params["material_item_ids"][0]
        _run(
            "record_srd_imprisonment_statuette",
            state,
            caster_state="hero",
            target_state="hero",
            material_id=material,
            depicts_target=True,
        )
        params.update(
            imprisonment_mode=mode,
            imprisonment_trigger="Place the silver leaf",
            imprisonment_trigger_observable=True,
            imprisonment_trigger_likely_decade=True,
            imprisonment_component_id=material,
            imprisonment_demiplane="prison",
        )
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
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
    else:
        hero.update(creature_type="fiend", home_plane="abyss")
    _run("cast_spell", state, participant_data=data, **params)
    supplies, data, travel_params = _case(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    supplied_hero = supplies[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"] = [spell]
    hero["spell_slot_cast_this_turn"] = False
    for lot in supplied_hero["spell_materials"]:
        lot["id"] = "travel_" + lot["id"]
        hero["spell_materials"].append(lot)
        hero["inventory"].append(lot["inventory_item"])
    travel_params["material_item_ids"] = [lot["id"] for lot in supplied_hero["spell_materials"]]
    if spell == "plane_shift":
        hero["plane_shift_linked_hands"] = ["hero"]
        hero["spell_materials"][-1]["attuned_plane"] = "material"
        anchor = next(
            entry
            for entry in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if entry["id"] == "orb"
        )
        anchor["movement"] = "passable"
        travel_params["travel_destination_subject"] = "orb"
    source_id = hero["timed_condition_source_ids"][-1]
    return state, data, travel_params, source_id


def _hound_travel_case(spell="dimension_door", destination=(311, 0, 0)):
    """Prepare the genuine anchor spell and later travel in the same admitted character."""
    state, data, hound = _case("faithful_hound")
    hound.pop("target")
    hound.update(
        force_id="hound_one",
        force_position=[10, 0, 0],
        hound_password="watch",
        hound_lighting="bright",
    )
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append(spell)
    data["spellcasting_grants"][0]["spells"].append(spell)
    data["wizard_spellbook"].append(spell)
    _, _, travel = _case(spell, travel_destination=list(destination))
    return state, data, hound, travel


@pytest.mark.parametrize(
    "spell,destination,active",
    [
        ("dimension_door", [310, 0, 0], True),
        ("dimension_door", [311, 0, 0], False),
        ("dimension_door", [10, 0, 300], True),
        ("dimension_door", [10, 0, 301], False),
        ("misty_step", [30, 0, 0], True),
    ],
)
def test_actual_hound_anchor_refreshes_after_named_travel(spell, destination, active):
    state, data, hound, travel = _hound_travel_case(spell, destination)
    _run("cast_spell", state, participant_data=data, **hound)
    assert state[f"{SRD}.state"]["spell_force_records"]["hound_one"]["active"] is True
    _run("cast_spell", state, participant_data=data, **travel)
    assert _point(state) == destination
    assert state[f"{SRD}.state"]["spell_force_records"]["hound_one"]["active"] is active
    proxy = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "hound_one"
    )
    assert proxy["active"] is active


def test_public_hound_cast_reopen_dimension_door_ends_actual_anchor_and_retry(
    monkeypatch,
    tmp_path,
):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, hound, travel = _hound_travel_case()
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, hound))
    database = tmp_path / "hound-travel.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "faithful_hound")
        mechanics = StatelessMcpMechanics(owner)
        cast = ActionCommand(
            campaign_id="casting",
            action_id="hound",
            idempotency_key="hound",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=hound,
        )
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.state"]["spell_force_records"]["hound_one"]["active"] is True
        assert mechanics.execute_action(cast) == receipt
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        cast = replace(
            cast,
            action_id="travel",
            idempotency_key="travel",
            expected_revision=saved.revision,
            parameters=travel,
        )
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _point(saved.state) == [311, 0, 0]
        assert saved.state[f"{SRD}.state"]["spell_force_records"]["hound_one"]["active"] is False
        proxy = next(
            e
            for e in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if e["id"] == "hound_one"
        )
        assert proxy["active"] is False
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 1
        assert mechanics.execute_action(cast) == receipt
        assert owner.snapshot("casting").state == saved.state


@pytest.mark.parametrize("mode", ["burial", "chaining", "hedged_prison"])
def test_actual_imprisonment_blocks_named_misty_step_until_its_observed_trigger(monkeypatch, mode):
    state, data, params, source_id = _progressive_travel_case(monkeypatch, "misty_step", mode=mode)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="imprisoned|chains"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    _run(
        "observe_srd_imprisonment_trigger",
        state,
        target_state="hero",
        source_id=source_id,
        observed_trigger="Place the silver leaf",
        trigger_observed=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    assert _point(state) == [30, 0, 0]


@pytest.mark.parametrize(
    "mode,message",
    [("chaining", "chains"), ("hedged_prison", "planar travel")],
)
def test_actual_imprisonment_blocks_ethereal_planar_transition_without_teleport_flag(
    monkeypatch,
    mode,
    message,
):
    state, data, params, _ = _progressive_travel_case(monkeypatch, "etherealness", mode=mode)
    if mode == "hedged_prison":
        next(
            entry
            for entry in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if entry["id"] == "hero"
        )["metadata"]["borders_ethereal"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=message):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def test_actual_imprisonment_chains_guard_ordinary_movement_before_cost_or_position(monkeypatch):
    state, _, _, _ = _progressive_travel_case(monkeypatch, "misty_step", mode="chaining")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="chains"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 5, 0])
    assert state == before


def test_actual_divine_word_ban_blocks_named_plane_shift_then_exact_expiry_allows_it(monkeypatch):
    state, data, params, _ = _progressive_travel_case(monkeypatch, "plane_shift")
    assert (
        next(
            entry
            for entry in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if entry["id"] == "hero"
        )["metadata"]["plane"]
        == "abyss"
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Divine Word"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    _run("cast_spell", state, participant_data=data, **params)
    assert _point(state) == [25, 0, 0]
    assert (
        next(
            entry
            for entry in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if entry["id"] == "hero"
        )["metadata"]["plane"]
        == "material"
    )


def test_public_misty_step_reopens_with_actual_prison_then_trigger_and_retry(monkeypatch, tmp_path):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, params, source_id = _progressive_travel_case(
        monkeypatch,
        "misty_step",
        mode="chaining",
    )
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / "travel-prison.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "misty_step")
        command = ActionCommand(
            campaign_id="casting",
            action_id="trapped-step",
            idempotency_key="trapped-step",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="chains"):
            StatelessMcpMechanics(owner).execute_action(command)
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").revision == 0
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == before.state
        mechanics = StatelessMcpMechanics(owner)
        freed = mechanics.execute_action(
            replace(
                command,
                action_id="prison-trigger",
                idempotency_key="prison-trigger",
                tool_name="observe_srd_imprisonment_trigger",
                parameters={
                    "target_state": "hero",
                    "source_id": source_id,
                    "observed_trigger": "Place the silver leaf",
                    "trigger_observed": True,
                },
            )
        )
        assert freed.status is ActionStatus.COMMITTED
        step = replace(
            command,
            action_id="free-step",
            idempotency_key="free-step",
            expected_revision=owner.snapshot("casting").revision,
        )
        receipt = mechanics.execute_action(step)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _point(saved.state) == [30, 0, 0]
        assert mechanics.execute_action(step) == receipt
        assert owner.snapshot("casting").state == saved.state


def test_misty_step_cast_uses_actual_range_and_occupancy_without_movement_cost():
    state, _, _, result = _cast("misty_step")
    assert _point(state) == [30, 0, 0]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    assert result.result["context"]["_spell_summary_rows"][-1]["value"] == "teleported"
    state, data, params = _case("misty_step", travel_destination=[31, 0, 0])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="range"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    params["travel_destination"] = [5, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="unoccupied"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize("lighting,visible", [("bright", True), ("dim", True), ("darkness", False)])
def test_misty_step_uses_actual_destination_ambient_and_saved_sight(lighting, visible):
    state, data, params = _case("misty_step")
    state[f"{SRD}.state"]["spatial_scene"]["zones"] = [_ambient_zone("material", lighting)]
    before = deepcopy(state)
    if visible:
        _run("cast_spell", state, participant_data=data, **params)
        assert _point(state) == [30, 0, 0]
    else:
        with pytest.raises(RulesetPreconditionRejected, match="actually see"):
            _run("cast_spell", state, participant_data=data, **params)
        assert state == before


@pytest.mark.parametrize("blindsight,visible", [(20, False), (30, True)])
def test_misty_step_saved_blindsight_uses_actual_destination_range(blindsight, visible):
    state, data, params = _case("misty_step")
    state[f"{SRD}.entities"]["hero"].update(conditions=["blinded"], blindsight_feet=blindsight)
    before = deepcopy(state)
    if visible:
        _run("cast_spell", state, participant_data=data, **params)
        assert _point(state) == [30, 0, 0]
    else:
        with pytest.raises(RulesetPreconditionRejected, match="actually see"):
            _run("cast_spell", state, participant_data=data, **params)
        assert state == before


@pytest.mark.parametrize("kind", ["missing", "conflicting"])
def test_misty_step_requires_actual_consistent_destination_ambient(kind):
    state, data, params = _case("misty_step")
    zones = state[f"{SRD}.state"]["spatial_scene"]["zones"]
    if kind == "missing":
        zones.clear()
    else:
        dark = _ambient_zone("material", "darkness")
        dark["id"] = "dark_ambient"
        zones.append(dark)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ambient lighting"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def test_misty_step_actual_fog_blocks_destination_until_source_clock_expiry():
    state, data, params = _case("fog_cloud", environment_origin=[15, 0, 0])
    _run("cast_spell", state, participant_data=data, **params)
    _, data, params = _case("misty_step")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"] = ["misty_step"]
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actually see"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _run("cast_spell", state, participant_data=data, **params)
    assert _point(state) == [30, 0, 0]


def _narrow_opening_case(spell="misty_step"):
    state, data, params = _case(spell, target="hero", targets=["hero"])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(entry for entry in scene["entities"] if entry["id"] == "hero")["footprint"] = {
        "kind": "box",
        "half_extents": [2, 2],
    }
    scene["geometry"] = []
    for identity, low, high in (("upper", 1, 6), ("lower", -6, -1)):
        wall = _wall(2, low_y=low, high_y=high)
        wall["id"] = identity
        wall["volume"].update(min=[4, low, -1], max=[6, high, 10])
        scene["geometry"].append(wall)
    return state, data, params


@pytest.mark.parametrize("spell", [None, "gaseous_form", "wind_walk"])
def test_gaseous_passes_narrow_opening_while_ordinary_and_wind_keep_body_clearance(spell):
    state, data, params = _narrow_opening_case(spell or "misty_step")
    if spell:
        if spell == "wind_walk":
            _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
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
        _run("cast_spell", state, participant_data=data, **params)
    before = deepcopy(state)
    if spell == "gaseous_form":
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="hero",
            destination=[8, 0, 0],
            mode="fly",
        )
        assert _point(state) == [8, 0, 0]
        assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 8
    else:
        with pytest.raises(RulesetPreconditionRejected, match="footprint"):
            _run(
                "move_srd_scene_creature",
                state,
                actor_state="hero",
                destination=[8, 0, 0],
                mode="fly" if spell else "walk",
            )
        assert state == before


def test_gaseous_solid_centerline_and_actual_map_bounds_still_block_without_cost():
    state, data, params = _narrow_opening_case("gaseous_form")
    _run("cast_spell", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"][0]["volume"]["min"][1] = -1
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="solid barrier"):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="hero",
            destination=[8, 0, 0],
            mode="fly",
        )
    assert state == before
    scene["geometry"] = []
    scene["entities"] = [entry for entry in scene["entities"] if entry["id"] == "hero"]
    scene["zones"] = []
    scene["map"]["bounds"]["max"][0] = 5
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="map bounds"):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="hero",
            destination=[8, 0, 0],
            mode="fly",
        )
    assert state == before


def test_gaseous_actual_expiry_removes_narrow_opening_permission_before_later_movement():
    state, data, params = _narrow_opening_case("gaseous_form")
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="footprint"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[8, 0, 0])
    assert state == before


def test_public_gaseous_narrow_opening_reopens_with_source_and_preserves_movement_retry(
    monkeypatch,
    tmp_path,
):
    import tests.srd_full.test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", lambda _spell: _narrow_opening_case("gaseous_form"))
    database = tmp_path / "travel-gaseous.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "gaseous_form")
        command = ActionCommand(
            campaign_id="casting",
            action_id="gaseous",
            idempotency_key="gaseous",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        move = replace(
            command,
            action_id="narrow-flight",
            idempotency_key="narrow-flight",
            expected_revision=saved.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "hero", "destination": [8, 0, 0], "mode": "fly"},
        )
        receipt = mechanics.execute_action(move)
        assert receipt.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert _point(after.state) == [8, 0, 0]
        assert mechanics.execute_action(move) == receipt
        assert owner.snapshot("casting").state == after.state


def test_dimension_door_blocked_companion_spends_cast_and_damages_both_without_moving():
    state, data, params = _case("dimension_door", targets=["guard"])
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(_entity("blocker", [35, 0, 0]))
    before = deepcopy(state)
    _run("cast_spell", state, participant_data=data, **params)
    assert _point(state) == _point(before)
    assert _point(state, "guard") == _point(before, "guard")
    hero = state[f"{SRD}.entities"]["hero"]
    guard = state[f"{SRD}.entities"]["guard"]
    assert hero["resources"]["spell_slot_4"] == 2
    assert 4 <= 500 - hero["hp"] <= 24
    assert hero["hp"] == guard["hp"]


@pytest.mark.parametrize(
    "actor_plane,wall_plane",
    [
        ("material", "ethereal"),
        ("ethereal", "material"),
    ],
)
def test_misty_step_destination_sight_selects_the_actual_caster_plane(actor_plane, wall_plane):
    state, data, params = _case("misty_step")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero = next(entity for entity in scene["entities"] if entity["id"] == "hero")
    hero["metadata"]["plane"] = actor_plane
    wall = _wall(4)
    wall["metadata"]["plane"] = wall_plane
    scene["geometry"] = [wall]
    _run("cast_spell", state, participant_data=data, **params)
    assert _point(state) == [30, 0, 0]


def test_dimension_door_companion_eligibility_and_success_survive_state_reopen():
    state, _, _, _ = _cast("dimension_door", targets=["guard"])
    reopened = deepcopy(state)
    assert _point(reopened) == [30, 0, 0] and _point(reopened, "guard") == [35, 0, 0]
    _run("move_srd_scene_creature", reopened, actor_state="hero", destination=[30, 5, 0])
    assert _point(reopened) == [30, 5, 0]
    state, data, params = _case("dimension_door", targets=["guard"])
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [6, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="range"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def test_word_of_recall_no_sanctuary_then_designated_anchor_persists():
    state, _, _, _ = _cast("word_of_recall")
    assert _point(state) == [0, 0, 0]
    state, data, params, _ = _cast("word_of_recall", travel_prepare_sanctuary=True)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 10, 0])
    state = deepcopy(state)
    params["travel_prepare_sanctuary"] = False
    _run("cast_spell", state, participant_data=data, **params)
    assert _point(state) == [0, 0, 0]


def _plants(state):
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for identity, point in (("oak1", [3, 0, 0]), ("oak2", [100, 0, 0])):
        plant = _entity(
            identity, point, inanimate_plant=True, living_tree=True, tree_kind="oak", size_rank=3
        )
        plant.update(movement="passable", visibility="clear")
        scene["entities"].append(plant)
    state[f"{SRD}.entities"]["hero"]["known_travel_plants"] = ["oak2"]


def test_transport_via_plants_shared_movement_lifetime_and_actual_planes():
    state, data, params = _case(
        "transport_via_plants", travel_origin_subject="oak1", travel_destination_subject="oak2"
    )
    _plants(state)
    _run("cast_spell", state, participant_data=data, **params)
    _run(
        "traverse_srd_plant_link",
        state,
        actor_state="hero",
        origin_subject="oak1",
        destination=[103, 0, 0],
    )
    assert _point(state) == [103, 0, 0]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 5
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="active link"):
        _run(
            "traverse_srd_plant_link",
            state,
            actor_state="hero",
            origin_subject="oak1",
            destination=[103, 0, 0],
        )
    assert state == before


def test_tree_stride_once_per_turn_size_kind_range_and_concentration():
    state, data, params = _case("tree_stride")
    _plants(state)
    _run("cast_spell", state, participant_data=data, **params)
    _run(
        "traverse_srd_tree_stride",
        state,
        actor_state="hero",
        origin_subject="oak1",
        destination_subject="oak2",
        destination=[103, 0, 0],
    )
    assert _point(state) == [103, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="once"):
        _run(
            "traverse_srd_tree_stride",
            state,
            actor_state="hero",
            origin_subject="oak2",
            destination_subject="oak1",
            destination=[0, 0, 0],
        )
    assert state == before
    _run("end_concentration", state, caster_state="hero")
    assert state[f"{SRD}.entities"]["hero"]["tree_stride"] is None


def test_profiles_bind_reviewed_source_exactly():
    validate_profiles(PROFILES)
    for name, definition in PROFILES.items():
        with pytest.raises(SystemExit, match="source profile"):
            validate_profiles({name: {**definition, "range_feet": definition["range_feet"] + 1}})


def test_actual_circle_sigil_study_requires_the_source_minute_before_learning():
    state, _, _ = _case("teleportation_circle")
    circle = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    circle["position"] = [5, 0, 0]
    circle["metadata"].update(teleportation_circle=True, sigil="new-sigil")
    before = deepcopy(state)
    with pytest.raises(PackageRuntimeError):
        _run("study_srd_circle_sigil", state, actor_state="hero", circle_subject="orb",
             elapsed_rounds=4, uninterrupted=True)
    assert state == before
    _run("study_srd_circle_sigil", state, actor_state="hero", circle_subject="orb",
         elapsed_rounds=10, uninterrupted=True)
    assert state[f"{SRD}.entities"]["hero"]["known_circle_sigils"] == ["new-sigil"]
    assert state[f"{SRD}.state"]["lasting_tick"] == 20


def test_plane_shift_requires_actual_plane_attuned_rod_and_linked_willing_members():
    state, data, params = _case("plane_shift", travel_destination_subject="orb", targets=["guard"])
    hero = state[f"{SRD}.entities"]["hero"]
    hero["plane_shift_linked_hands"] = ["hero", "guard"]
    hero["spell_materials"][0]["attuned_plane"] = "fire"
    orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    orb["metadata"]["plane"] = "fire"
    orb["movement"] = "passable"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="willing"):
        _run(
            "cast_spell", state, participant_data=data, **(params | {"all_targets_willing": False})
        )
    assert state == before
    hero["spell_materials"][0]["attuned_plane"] = "water"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="rod"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    hero["spell_materials"][0]["attuned_plane"] = "fire"
    _run("cast_spell", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert all(entity["metadata"]["plane"] == "fire" for entity in scene["entities"][:2])
    assert _point(state) != _point(state, "guard")


def test_teleportation_circle_uses_actual_known_same_plane_circle_and_later_entry():
    state, data, params = _case(
        "teleportation_circle", travel_destination_subject="orb", travel_destination=[0, 0, 0]
    )
    hero = state[f"{SRD}.entities"]["hero"]
    hero["known_circle_sigils"] = ["sigil-test"]
    orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    orb["metadata"].update(teleportation_circle=True, sigil="sigil-test")
    orb.update(movement="passable", visibility="clear")
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
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
    _run("cast_spell", state, participant_data=data, **params)
    _run("enter_srd_teleportation_circle", state, actor_state="hero", caster_state="hero")
    assert _point(state) == orb["position"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    with pytest.raises(RulesetPreconditionRejected, match="closed"):
        _run("enter_srd_teleportation_circle", state, actor_state="guard", caster_state="hero")


def test_public_dimension_door_archive_admission_retry_reopen_and_later_movement(
    tmp_path, monkeypatch
):
    import tests.srd_full.test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", lambda spell: _case(spell, targets=["guard"]))
    database = tmp_path / "travel.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "dimension_door")
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="door",
            idempotency_key="door",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _point(saved.state) == [30, 0, 0]
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        moved = StatelessMcpMechanics(owner).execute_action(
            replace(
                command,
                action_id="walk",
                idempotency_key="walk",
                expected_revision=saved.revision,
                tool_name="move_srd_scene_creature",
                parameters={"actor_state": "hero", "destination": [30, 5, 0]},
            )
        )
        assert moved.status is ActionStatus.COMMITTED
        assert _point(owner.snapshot("casting").state) == [30, 5, 0]


def test_additional_travelers_are_unique_and_exclude_the_caster():
    for targets, rejection, message in (
        (["guard", "guard"], PackageRuntimeError, "execution failed"),
        (["hero"], RulesetPreconditionRejected, "once, excluding"),
    ):
        state, data, params = _case("dimension_door", targets=targets)
        before = deepcopy(state)
        with pytest.raises(rejection, match=message):
            _run("cast_spell", state, participant_data=data, **params)
        assert state == before


def test_plane_shift_checks_actual_adjacent_contact_including_the_closing_link():
    state, data, params = _case(
        "plane_shift", travel_destination_subject="orb", targets=["guard", "ally"]
    )
    hero = state[f"{SRD}.entities"]["hero"]
    hero["plane_shift_linked_hands"] = ["hero", "guard", "ally"]
    hero["spell_materials"][0]["attuned_plane"] = "fire"
    state[f"{SRD}.entities"]["ally"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][2]["metadata"]["plane"] = "fire"
    scene["entities"][2]["movement"] = "passable"
    scene["entities"].append(_entity("ally", [10, 0, 0]))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual same-plane range"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    scene["entities"][-1]["position"] = [2.5, 4, 0]
    _run("cast_spell", state, participant_data=data, **params)
    assert all(
        entity["metadata"]["plane"] == "fire"
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
    )


def test_tree_stride_last_five_feet_return_to_origin_and_reveal_actual_tree_locations():
    state, data, params = _case("tree_stride")
    _plants(state)
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.entities"]["hero"]["scene_movement_tick"] = 0
    state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] = 25
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for subject, point, changes in (
        ("far", [600, 0, 0], {}),
        ("wrong-kind", [50, 0, 0], {"tree_kind": "pine"}),
        ("other-plane", [20, 0, 0], {"plane": "ethereal"}),
    ):
        tree = deepcopy(scene["entities"][-1])
        tree.update(id=subject, position=point)
        tree["metadata"].update(changes)
        scene["entities"].append(tree)
    result = _run(
        "traverse_srd_tree_stride",
        state,
        actor_state="hero",
        origin_subject="oak1",
        destination_subject="oak2",
        destination=[3, 5, 0],
    )
    assert _point(state) == [3, 5, 0]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 30
    assert result.result["context"]["_travel_cost"] == 5
    assert result.result["context"]["_travel_tree_locations"] == [
        {"subject": "oak2", "position": [100, 0, 0]}
    ]


def test_circle_stays_open_through_the_casters_next_turn_and_closes_at_its_actual_end():
    state, data, params = _case(
        "teleportation_circle", travel_destination_subject="orb", travel_destination=[0, 0, 0]
    )
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(known_circle_sigils=["sigil-test"], turns_started=7)
    circle = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    circle["metadata"].update(teleportation_circle=True, sigil="sigil-test")
    circle.update(movement="passable", visibility="clear")
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
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
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=10)
    _run("end_combat_turn", state, actor="hero")
    assert state[f"{SRD}.state"]["travel_circles"][-1]["active"] is True
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    _run("enter_srd_teleportation_circle", state, actor_state="hero", caster_state="hero")
    assert _point(state) == circle["position"]
    _run("end_combat_turn", state, actor="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="closed"):
        _run("enter_srd_teleportation_circle", state, actor_state="guard", caster_state="hero")
    assert state == before


@pytest.mark.parametrize("slot,additional", [(7, 0), (8, 2), (9, 5)])
def test_etherealness_higher_slots_include_the_caster_in_the_three_per_slot_limit(slot, additional):
    state, data, params = _case("etherealness")
    targets = []
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for index in range(additional):
        subject = f"traveler{index}"
        targets.append(subject)
        state[f"{SRD}.entities"][subject] = deepcopy(state[f"{SRD}.entities"]["guard"])
        scene["entities"].append(_entity(subject, [2 * (index + 1), 0, 0]))
    params.update(slot_resource=f"spell_slot_{slot}", targets=targets or None)
    if targets:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="willing"):
            _run(
                "cast_spell",
                state,
                participant_data=data,
                **(params | {"all_targets_willing": False}),
            )
        assert state == before
    _run("cast_spell", state, participant_data=data, **params)
    ethereal = [
        entry
        for entry in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entry["metadata"]["plane"] == "ethereal"
    ]
    assert len(ethereal) == additional + 1
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    for subject in ["hero", *targets]:
        assert state[f"{SRD}.entities"][subject]["travel_ethereal"]["return_plane"] == "material"
        assert state[f"{SRD}.entities"][subject]["travel_ethereal"]["caster"] == "hero"


@pytest.mark.parametrize("plane", ["ethereal", "abyss"])
def test_etherealness_cast_on_a_nonborder_plane_ends_after_actual_casting_costs(plane):
    state, data, params = _case("etherealness")
    state[f"{SRD}.state"]["spatial_scene"]["entities"][0]["metadata"]["plane"] = plane
    _run("cast_spell", state, participant_data=data, **params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_7"] == 2
    assert hero.get("travel_ethereal") is None
    assert state[f"{SRD}.state"]["spatial_scene"]["entities"][0]["metadata"]["plane"] == plane


def test_ethereal_movement_uses_actual_planes_and_extra_vertical_movement_cost():
    state, data, params = _case("etherealness")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(4)]
    _run("cast_spell", state, participant_data=data, **params)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 5])
    assert _point(state) == [15, 0, 5]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 25
    wall = _wall(4)
    wall.update(
        id="ethereal-wall", volume={"kind": "box", "min": [16, -10, 0], "max": [20, 10, 20]}
    )
    wall["metadata"]["plane"] = "ethereal"
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="solid barrier"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[22, 0, 5])
    assert state == before


def test_border_ethereal_perception_stops_at_actual_sixty_feet_without_same_plane_interaction():
    state, data, params = _case("etherealness")
    _run("cast_spell", state, participant_data=data, **params)
    guard = next(
        entity
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == "guard"
    )
    guard["position"] = [60, 0, 0]
    result = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert result.result["context"]["_scene_can_see"] is True
    assert result.result["context"]["_scene_same_plane"] is False
    guard["position"] = [61, 0, 0]
    result = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert result.result["context"]["_scene_can_see"] is False


@pytest.mark.parametrize("plane", ["material", "ethereal"])
def test_actual_movement_rejects_footprint_wall_overlap_and_allows_surface_tangency(plane):
    state, data, params = _case("etherealness")
    if plane == "ethereal":
        _run("cast_spell", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = _wall(4, low_y=0.3, high_y=3)
    wall["metadata"]["plane"] = plane
    scene["geometry"] = [wall]
    next(entity for entity in scene["entities"] if entity["id"] == "guard")["position"] = [5, 5, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="movement footprint"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[20, 0, 0])
    assert state == before
    wall["volume"]["min"][1] = 0.4
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[20, 0, 0])
    assert _point(state) == [20, 0, 0]


def test_ethereal_vertical_movement_uses_actual_height_for_ceiling_clearance():
    state, data, params = _case("etherealness")
    _run("cast_spell", state, participant_data=data, **params)
    ceiling = _wall(4)
    ceiling.update(id="ceiling", volume={"kind": "box", "min": [-5, -5, 8], "max": [5, 5, 9]})
    ceiling["metadata"]["plane"] = "ethereal"
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [ceiling]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 0, 2])
    assert _point(state) == [0, 0, 2]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 4
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="movement footprint"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 0, 2.1])
    assert state == before


def test_source_movement_rejects_selected_curves_without_spending_or_repositioning():
    state, _, _ = _case("misty_step")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(entity for entity in scene["entities"] if entity["id"] == "hero")["footprint"] = {
        "kind": "circle",
        "radius": 0.5,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="selected curved geometry"):
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 5, 0])
    assert state == before


def _ethereal_shunt_case():
    state, data, params = _case("etherealness")
    wall = _wall(4)
    wall["volume"] = {"kind": "box", "min": [-10, -10, -10], "max": [10, 10, 5]}
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    return state, data, params


def test_etherealness_shared_clock_returns_at_actual_position_and_damages_twice_exact_shunt_feet():
    state, data, params = _ethereal_shunt_case()
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=4799)
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is not None
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _point(state) == [0, 0, 5]
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 490
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None
    assert state[f"{SRD}.state"]["travel_phase_targets"] == []


def test_etherealness_caster_dismissal_returns_upcast_group_and_applies_actual_shunts():
    state, data, params = _ethereal_shunt_case()
    params.update(slot_resource="spell_slot_8", targets=["guard"], all_targets_willing=True)
    _run("cast_spell", state, participant_data=data, **params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["travel_ethereal"]["slot_level"] == 8
    result = _run("dismiss_srd_etherealness", state, caster_state="hero")
    assert result.result["context"]["_travel_dismiss_count"] == 2
    assert state[f"{SRD}.state"]["lasting_tick"] == 0
    assert state[f"{SRD}.state"]["travel_phase_targets"] == []
    for subject in ("hero", "guard"):
        target = state[f"{SRD}.entities"][subject]
        assert target["travel_ethereal"] is None and target["hp"] == 490
        assert _point(state, subject)[2] == 5
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no ongoing"):
        _run("dismiss_srd_etherealness", state, caster_state="hero")
    assert state == before


def test_etherealness_dismissal_preserves_other_casters_actual_grants():
    state, data, params = _case("etherealness")
    _run(
        "apply_srd_etherealness_target",
        state,
        target_state="guard",
        caster_state="guard",
        slot_level=7,
    )
    _run("cast_spell", state, participant_data=data, **params)
    saved_guard = deepcopy(state[f"{SRD}.entities"]["guard"]["travel_ethereal"])
    _run("dismiss_srd_etherealness", state, caster_state="hero")
    assert state[f"{SRD}.entities"]["guard"]["travel_ethereal"] == saved_guard
    assert state[f"{SRD}.state"]["travel_phase_targets"] == ["guard"]
    guard = next(
        entity
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == "guard"
    )
    assert guard["metadata"]["plane"] == "ethereal"


def test_etherealness_dismissal_in_combat_spends_no_action_and_rejects_incapacitation():
    state, data, params = _case("etherealness")
    _run("cast_spell", state, participant_data=data, **params)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["conditions"].append("incapacitated")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Incapacitated"):
        _run("dismiss_srd_etherealness", state, caster_state="hero")
    assert state == before
    hero["conditions"].remove("incapacitated")
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        action_available=True,
    )
    _run("dismiss_srd_etherealness", state, caster_state="hero")
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None


def test_travel_perception_qualifies_actual_plane_and_live_return_plane_with_actual_range():
    state, data, params = _case("etherealness")
    _run("cast_spell", state, participant_data=data, **params)
    result = _run(
        "resolve_srd_travel_perception",
        state,
        observer_state="hero",
        point=[60, 0, 0],
        perception_plane="material",
    ).result["context"]
    assert result["_travel_perception_plane"] == "material"
    assert result["_travel_perception_observer_plane"] == "ethereal"
    assert result["_travel_perception_origin"] == [0, 0, 0]
    assert result["_travel_perception_distance"] == 60
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="sixty"):
        _run(
            "resolve_srd_travel_perception",
            state,
            observer_state="hero",
            point=[61, 0, 0],
            perception_plane="material",
        )
    assert state == before
    normal = _run(
        "resolve_srd_travel_perception",
        state,
        observer_state="hero",
        point=[61, 0, 0],
    ).result["context"]
    assert normal["_travel_perception_plane"] == "ethereal"
    assert normal["_travel_perception_distance"] == 61


@pytest.mark.parametrize("phase", [False, True])
def test_travel_perception_rejects_unsourced_planar_projection(phase):
    state, data, params = _case("etherealness")
    if phase:
        _run("cast_spell", state, participant_data=data, **params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no active source"):
        _run(
            "resolve_srd_travel_perception",
            state,
            observer_state="hero",
            point=[10, 0, 0],
            perception_plane="abyss",
        )
    assert state == before


def test_travel_perception_refreshes_expired_source_through_actual_shunt_before_qualification():
    state, data, params = _ethereal_shunt_case()
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.state"]["lasting_tick"] = 9600
    result = _run(
        "resolve_srd_travel_perception",
        state,
        observer_state="hero",
        point=[0, 0, 65],
        perception_plane="material",
    ).result["context"]
    assert result["_travel_perception_observer_plane"] == "material"
    assert result["_travel_perception_origin"] == [0, 0, 5]
    assert result["_travel_perception_distance"] == 60
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 490


def test_travel_perception_rejects_a_different_source_in_the_projection_record():
    state, data, params = _case("etherealness")
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["spell"] = "blink"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no active source"):
        _run(
            "resolve_srd_travel_perception",
            state,
            observer_state="hero",
            point=[10, 0, 0],
            perception_plane="material",
        )
    assert state == before


@pytest.mark.parametrize("ending", ["expiry", "dismissal"])
def test_public_etherealness_reopens_on_actual_plane_and_later_ending_applies_the_exact_shunt(
    tmp_path, monkeypatch, ending
):
    import tests.srd_full.test_srd_public_spell_casting as public

    monkeypatch.setattr(public, "_spell_case", lambda spell: _ethereal_shunt_case())
    database = tmp_path / "ethereal.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "etherealness")
        command = ActionCommand(
            campaign_id="casting",
            action_id="ethereal",
            idempotency_key="ethereal",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["return_plane"] == "material"
        )
        assert mechanics.execute_action(command) == receipt
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        returned = StatelessMcpMechanics(owner).execute_action(
            replace(
                command,
                action_id="ethereal-end",
                idempotency_key="ethereal-end",
                expected_revision=saved.revision,
                tool_name=(
                    "advance_srd_lasting_time" if ending == "expiry" else "dismiss_srd_etherealness"
                ),
                parameters=(
                    {"elapsed_rounds": 4800} if ending == "expiry" else {"caster_state": "hero"}
                ),
            )
        )
        assert returned.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting").state
        assert _point(final) == [0, 0, 5]
        assert final[f"{SRD}.entities"]["hero"]["hp"] == 490


def _plane(state, subject="hero"):
    return next(
        entity["metadata"]["plane"]
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == subject
    )



@pytest.mark.parametrize("kind", ["object", "spell_force"])
def test_shared_relocation_moves_typed_scene_only_anchor_without_creature_state(kind):
    state, _, _ = _case("misty_step")
    metadata = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"]
    if kind == "spell_force":
        metadata.pop("is_object")
        metadata["creature_type"] = "spell_force"
    before_entities = deepcopy(state[f"{SRD}.entities"])
    _run(
        "move_srd_travel_subject",
        state,
        subject="orb",
        destination=[40, 0, 3],
        plane="ethereal",
        teleport=False,
    )
    assert _point(state, "orb") == [40, 0, 3] and _plane(state, "orb") == "ethereal"
    assert state[f"{SRD}.entities"] == before_entities
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == 1



def test_shared_relocation_rejects_untyped_scene_only_anchor_without_creature_state():
    state, _, _ = _case("misty_step")
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"].pop("is_object")
    before = deepcopy(state)
    with pytest.raises(PackageRuntimeError):
        _run(
            "move_srd_travel_subject",
            state,
            subject="orb",
            destination=[40, 0, 3],
            plane="material",
            teleport=False,
        )
    assert state == before



@pytest.mark.parametrize("participant", [True, False])
def test_shared_relocation_preserves_actual_creature_guard_with_or_without_participant_ref(
    monkeypatch,
    participant,
):
    state, _, _, _ = _progressive_travel_case(monkeypatch, "misty_step", mode="chaining")
    metadata = state[f"{SRD}.state"]["spatial_scene"]["entities"][0]["metadata"]
    if participant:
        metadata["is_object"] = True
    else:
        metadata.pop("participant_ref")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="chains"):
        _run(
            "move_srd_travel_subject",
            state,
            subject="hero",
            destination=[10, 0, 0],
            plane="material",
            teleport=False,
        )
    assert state == before


def _blink_case(monkeypatch, rolls=(4,), *, plane="material"):
    """Cast the named spell, then expose genuine caster end/start boundaries."""
    actual_dice = _TravelDice([(6, value) for value in rolls])
    monkeypatch.setattr(dice, "_rng", actual_dice)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _case("blink")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["turns_started"] = 1
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][0]["metadata"]["plane"] = plane
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        phase="turn_active",
    )
    return state, data, params, actual_dice



@pytest.mark.parametrize("roll", [1, 3, 4, 6])
def test_blink_source_d6_phases_only_four_through_six_at_actual_end_and_returns_next_start(
    monkeypatch,
    roll,
):
    state, data, _, actual_dice = _blink_case(monkeypatch, (roll,))
    hero = state[f"{SRD}.entities"]["hero"]
    assert _plane(state) == "material" and hero["travel_blink"]["last_roll"] is None
    assert hero["resources"]["spell_slot_3"] == 2
    _run("end_combat_turn", state, actor="hero")
    assert _plane(state) == ("ethereal" if roll >= 4 else "material")
    assert hero["resources"]["spell_slot_3"] == 2
    record = state[f"{SRD}.entities"]["hero"]["travel_blink"]
    assert record["last_roll"]["results"] == [roll] and record["last_roll_turn"] == 1
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert _plane(state) == "material" and _point(state) == [0, 0, 0]
    assert state[f"{SRD}.entities"]["hero"]["travel_blink"]["phase"] is False
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    assert actual_dice.consumed == [(6, roll)]



def test_blink_saved_turn_roll_is_not_repeated_by_same_boundary_and_later_turn_rolls_once(
    monkeypatch,
):
    state, data, _, actual_dice = _blink_case(monkeypatch, (3, 6))
    _run("end_combat_turn", state, actor="hero")
    before = deepcopy(state)
    _run("end_srd_blink_turn", state, target_state="hero", boundary_turn=1)
    assert state == before and actual_dice.consumed == [(6, 3)]
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    _run("end_combat_turn", state, actor="hero")
    assert _plane(state) == "ethereal"
    assert state[f"{SRD}.entities"]["hero"]["travel_blink"]["last_roll_turn"] == 2
    assert actual_dice.consumed == [(6, 3), (6, 6)]



def test_blink_actual_three_dimensional_return_choice_has_ten_foot_limit_and_no_budget_cost(
    monkeypatch,
):
    state, data, _, _ = _blink_case(monkeypatch)
    _run("end_combat_turn", state, actor="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="within ten"):
        _run("choose_srd_blink_return_point", state, target_state="hero", destination=[6, 0, 9])
    assert state == before
    _run("choose_srd_blink_return_point", state, target_state="hero", destination=[6, 0, 8])
    assert _point(state) == [0, 0, 0] and _plane(state) == "ethereal"
    assert state[f"{SRD}.combat"] == before[f"{SRD}.combat"]
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert _point(state) == [6, 0, 8] and _plane(state) == "material"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2



def test_blink_revalidates_occupied_choice_at_actual_return_without_leaking_turn_or_position(
    monkeypatch,
):
    state, data, _, _ = _blink_case(monkeypatch)
    _run("end_combat_turn", state, actor="hero")
    _run("choose_srd_blink_return_point", state, target_state="hero", destination=[10, 0, 0])
    _run(
        "move_srd_travel_subject", state, subject="guard", destination=[10, 0, 0], plane="material"
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="occupied"):
        _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert state == before
    _run("choose_srd_blink_return_point", state, target_state="hero", destination=[0, 10, 0])
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert _point(state) == [0, 10, 0] and _plane(state) == "material"



@pytest.mark.parametrize("obstruction", ["blinded", "missing_ambient", "solid"])
def test_blink_return_choice_uses_actual_saved_sight_and_destination_plane_geometry(
    monkeypatch,
    obstruction,
):
    state, _, _, _ = _blink_case(monkeypatch)
    _run("end_combat_turn", state, actor="hero")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if obstruction == "blinded":
        state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    elif obstruction == "missing_ambient":
        scene["zones"] = [z for z in scene["zones"] if z["metadata"].get("plane") != "material"]
    else:
        scene["geometry"] = [
            {
                **_wall(1),
                "volume": {"kind": "box", "min": [4, -10, -10], "max": [5, 10, 10]},
            }
        ]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="see|ambient"):
        _run("choose_srd_blink_return_point", state, target_state="hero", destination=[10, 0, 0])
    assert state == before



def test_blink_projection_uses_live_source_actual_origin_and_sixty_foot_limit(monkeypatch):
    state, _, _, _ = _blink_case(monkeypatch)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="active source"):
        _run(
            "resolve_srd_travel_perception",
            state,
            observer_state="hero",
            point=[0, 0, 0],
            perception_plane="ethereal",
        )
    assert state == before
    _run("end_combat_turn", state, actor="hero")
    result = _run(
        "resolve_srd_travel_perception",
        state,
        observer_state="hero",
        point=[60, 0, 0],
        perception_plane="material",
    )
    assert result.result["context"]["_travel_perception_distance"] == 60
    assert result.result["context"]["_travel_perception_origin"] == [0, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="sixty"):
        _run(
            "resolve_srd_travel_perception",
            state,
            observer_state="hero",
            point=[61, 0, 0],
            perception_plane="material",
        )
    assert state == before



def test_blink_actual_clock_expiry_returns_phased_target_and_ends_source(monkeypatch):
    state, _, _, _ = _blink_case(monkeypatch)
    _run("end_combat_turn", state, actor="hero")
    _run("choose_srd_blink_return_point", state, target_state="hero", destination=[0, 10, 0])
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=19)
    assert _plane(state) == "ethereal"
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    assert _point(state) == [0, 10, 0] and _plane(state) == "material"
    assert state[f"{SRD}.entities"]["hero"]["travel_blink"] is None



def test_blink_no_unoccupied_space_within_ten_uses_proved_nearest_three_dimensional_fallback(
    monkeypatch,
):
    state, _, _, _ = _blink_case(monkeypatch)
    _run("end_combat_turn", state, actor="hero")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [
        {
            **_wall(1),
            "volume": {"kind": "box", "min": [-20, -20, -20], "max": [20, 20, 11]},
        }
    ]
    state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    _run("end_srd_blink_target", state, target_state="hero")
    assert _point(state) == [0, 0, 11] and _plane(state) == "material"
    assert state[f"{SRD}.entities"]["hero"]["travel_blink"] is None



def test_blink_original_occupied_but_another_space_within_range_requires_actual_visible_choice(
    monkeypatch,
):
    state, data, _, _ = _blink_case(monkeypatch)
    _run("end_combat_turn", state, actor="hero")
    _run("move_srd_travel_subject", state, subject="guard", destination=[0, 0, 0], plane="material")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Choose an actual visible"):
        _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert state == before
    _run("choose_srd_blink_return_point", state, target_state="hero", destination=[0, 10, 0])
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert _point(state) == [0, 10, 0] and _plane(state) == "material"



def test_blink_roll_on_already_ethereal_plane_ends_without_inventing_return_anchor(monkeypatch):
    state, _, _, _ = _blink_case(monkeypatch, plane="ethereal")
    _run("end_combat_turn", state, actor="hero")
    assert state[f"{SRD}.entities"]["hero"]["travel_blink"] is None
    assert _plane(state) == "ethereal" and _point(state) == [0, 0, 0]



def test_blink_selected_curved_arrival_rejects_without_leaking_return_or_next_turn(monkeypatch):
    state, data, _, _ = _blink_case(monkeypatch)
    _run("end_combat_turn", state, actor="hero")
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["footprint"] = {
        "kind": "circle",
        "radius": 1,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="curved"):
        _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert state == before



def test_blink_actual_fog_blocks_return_choice_until_exact_concentration_removal(monkeypatch):
    actual_dice = _TravelDice([(6, 4)])
    monkeypatch.setattr(dice, "_rng", actual_dice)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _case("fog_cloud", environment_origin=[5, 0, 0])
    _run("cast_spell", state, participant_data=data, **params)
    _, data, params = _case("blink")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(prepared_spells=["blink"], spell_slot_cast_this_turn=False, turns_started=1)
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        phase="turn_active",
    )
    _run("end_combat_turn", state, actor="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="see"):
        _run("choose_srd_blink_return_point", state, target_state="hero", destination=[0, 10, 0])
    assert state == before
    _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
    _run("choose_srd_blink_return_point", state, target_state="hero", destination=[0, 10, 0])
    _run("begin_combat_turn", state, actor="hero", participant_data=data)
    assert _plane(state) == "material" and _point(state) == [0, 10, 0]



@pytest.mark.parametrize("spell", ["blink", "etherealness"])
def test_pure_travel_projection_rejects_expired_source_without_refresh_or_relocation(
    monkeypatch,
    spell,
):
    if spell == "blink":
        state, _, _, _ = _blink_case(monkeypatch)
        _run("end_combat_turn", state, actor="hero")
        expires = state[f"{SRD}.entities"]["hero"]["travel_blink"]["expires_tick"]
    else:
        state, data, params = _ethereal_shunt_case()
        _run("cast_spell", state, participant_data=data, **params)
        expires = state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["expires_tick"]
    before = deepcopy(state)
    result = _run(
        "qualify_srd_travel_perception",
        state,
        observer_state="hero",
        point=[0, 0, 0],
        perception_plane="material",
    ).result["context"]
    assert result["_travel_perception_observer_plane"] == "ethereal"
    assert result["_travel_perception_plane"] == "material" and state == before
    state[f"{SRD}.state"]["lasting_tick"] = expires
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="active source"):
        _run(
            "qualify_srd_travel_perception",
            state,
            observer_state="hero",
            point=[0, 0, 0],
            perception_plane="material",
        )
    assert state == before and _plane(state) == "ethereal"



def test_pure_travel_perception_missing_plane_uses_neutral_material_default_without_projection():
    state, _, _ = _case("misty_step")
    state[f"{SRD}.state"]["spatial_scene"]["entities"][0]["metadata"].pop("plane")
    before = deepcopy(state)
    result = _run(
        "qualify_srd_travel_perception",
        state,
        observer_state="hero",
        point=[61, 0, 0],
        perception_plane="material",
    ).result["context"]
    assert result["_travel_perception_plane"] == "material"
    assert result["_travel_perception_observer_plane"] == "material"
    assert result["_travel_perception_distance"] == 61 and state == before
    with pytest.raises(RulesetPreconditionRejected, match="active source"):
        _run(
            "qualify_srd_travel_perception",
            state,
            observer_state="hero",
            point=[0, 0, 0],
            perception_plane="ethereal",
        )
    assert state == before



def test_public_blink_paid_cast_actual_turn_roll_reopen_choice_return_and_retry(
    monkeypatch, tmp_path
):
    import tests.srd_full.test_srd_public_spell_casting as public

    actual_dice = _TravelDice([(20, 1), (20, 2), (20, 3), (6, 4)])
    monkeypatch.setattr(dice, "_rng", actual_dice)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _case("blink")
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / "blink.sqlite3"
    cast = ActionCommand(
        campaign_id="casting",
        action_id="blink",
        idempotency_key="blink",
        owner_id="casting-owner",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="casting-turn",
        package_id=SRD,
        tool_name="cast_spell_outside_combat",
        parameters=params,
    )

    def act(owner, mechanics, name, label, **parameters):
        command = replace(
            cast,
            action_id=label,
            idempotency_key=label,
            tool_name=name,
            expected_revision=owner.snapshot("casting").revision,
            parameters=parameters,
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        return saved

    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "blink")
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert mechanics.execute_action(cast) == receipt
        assert owner.snapshot("casting").state == saved.state
        act(
            owner,
            mechanics,
            "start_group_combat_encounter",
            "encounter",
            combatants=["hero", "guard", "other"],
        )
        act(owner, mechanics, "begin_combat_turn", "first-start")
        saved = act(owner, mechanics, "end_combat_turn", "phase")
        assert _plane(saved.state) == "ethereal" and _point(saved.state) == [0, 0, 0]
        assert saved.state[f"{SRD}.entities"]["hero"]["travel_blink"]["last_roll"]["results"] == [4]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        saved = act(
            owner,
            mechanics,
            "choose_srd_blink_return_point",
            "choose",
            target_state="hero",
            destination=[0, 10, 0],
        )
        assert _plane(saved.state) == "ethereal" and _point(saved.state) == [0, 0, 0]
        for index in range(2):
            act(owner, mechanics, "advance_group_combat_encounter_turn", f"advance-{index}")
            act(owner, mechanics, "begin_combat_turn", f"npc-start-{index}")
            act(owner, mechanics, "end_combat_turn", f"npc-end-{index}")
        act(owner, mechanics, "advance_group_combat_encounter_turn", "next-caster")
        saved = act(owner, mechanics, "begin_combat_turn", "return")
        assert _plane(saved.state) == "material" and _point(saved.state) == [0, 10, 0]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert saved.state[f"{SRD}.combat"]["movement_remaining"] == 30
        assert actual_dice.consumed == [(20, 1), (20, 2), (20, 3), (6, 4)]


def _exile_case(monkeypatch, spell="maze", rolls=()):
    actual = _TravelDice([(20, value) for value in rolls])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _case(spell, targets=["guard"])
    state[f"{SRD}.entities"]["guard"].update(
        creature_kind="monster",
        skill_check_modifiers={"investigation": 5},
    )
    return state, data, params, actual



@pytest.mark.parametrize("roll,modifier,exiled", [(1, 0, True), (20, 0, False), (1, 30, False)])
def test_banishment_actual_charisma_save_exiles_only_failure(monkeypatch, roll, modifier, exiled):
    state, data, params, actual = _exile_case(monkeypatch, "banishment", [roll])
    target = state[f"{SRD}.entities"]["guard"]
    target["saving_throw_modifiers"]["charisma"] = modifier
    _run("cast_spell", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    assert actual.consumed == [(20, roll)]
    assert target["last_banishment_save"]["modifier"] == modifier
    assert (_plane(state, "guard") != "material") is exiled
    assert ("incapacitated" in target["conditions"]) is exiled
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    if exiled:
        grant = target["travel_exile"]
        assert grant["return_position"] == [5, 0, 0] and grant["return_plane"] == "material"
        assert grant["expires_tick"] - grant["created_tick"] == 20
        assert _point(state, "guard") == [5, 0, 0]



def test_banishment_condition_immunity_preserves_exile_and_real_save(monkeypatch):
    state, data, params, actual = _exile_case(monkeypatch, "banishment", [1])
    target = state[f"{SRD}.entities"]["guard"]
    target["condition_immunities"] = ["incapacitated"]
    _run("cast_spell", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    assert actual.consumed == [(20, 1)] and _plane(state, "guard").startswith("srd_exile:")
    assert "incapacitated" not in target["conditions"]
    assert target["travel_exile"]["condition_source_id"] is None



@pytest.mark.parametrize("creature_type", ["aberration", "celestial", "elemental", "fey", "fiend"])
def test_banishment_associated_plane_gap_rejects_actual_body_before_payment(
    monkeypatch, creature_type
):
    state, data, params, actual = _exile_case(monkeypatch, "banishment")
    state[f"{SRD}.entities"]["guard"]["creature_type"] = creature_type
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="associated-plane"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before and actual.consumed == []



@pytest.mark.parametrize("spell,ticks", [("banishment", 20), ("maze", 200)])
def test_exile_exact_clock_return_preserves_unowned_incapacitated(monkeypatch, spell, ticks):
    state, data, params, actual = _exile_case(
        monkeypatch, spell, [1] if spell == "banishment" else []
    )
    if spell == "banishment":
        state[f"{SRD}.entities"]["guard"]["conditions"] = ["incapacitated"]
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=ticks - 1)
    assert _plane(state, "guard").startswith("srd_exile:")
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    target = state[f"{SRD}.entities"]["guard"]
    assert _plane(state, "guard") == "material" and _point(state, "guard") == [5, 0, 0]
    assert target["travel_exile"] is None
    assert ("incapacitated" in target["conditions"]) is (spell == "banishment")
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None



@pytest.mark.parametrize("position,expected", [([5, 0, 0], [5, -0.8, 0]), ([5, 0, -6], [5, 0, 0])])
def test_exile_return_uses_actual_plane_occupancy_and_nearest_box(monkeypatch, position, expected):
    state, data, params, _ = _exile_case(monkeypatch)
    _run("cast_spell", state, participant_data=data, **params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(_entity("occupant", position, creature_type="humanoid"))
    _run("end_srd_concentration_voluntarily", state, caster_state="hero")
    assert _plane(state, "guard") == "material" and _point(state, "guard") == expected
    assert state[f"{SRD}.entities"]["guard"]["travel_exile"] is None



@pytest.mark.parametrize("roll,success", [(14, False), (15, True)])
def test_maze_real_npc_study_dc20_cost_and_source_return(monkeypatch, roll, success):
    state, data, params, actual = _exile_case(monkeypatch, rolls=[roll])
    _run("cast_spell", state, participant_data=data, **params)
    assert actual.consumed == []
    target = state[f"{SRD}.entities"]["guard"]
    assert "incapacitated" not in target["conditions"]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
        action_surge_available=False,
    )
    _run("study_srd_maze_escape", state, actor_state="guard", maze_proficiency_factor="none")
    target = state[f"{SRD}.entities"]["guard"]
    assert actual.consumed == [(20, roll)]
    assert target["last_maze_study"]["total"] == roll + 5
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert (_plane(state, "guard") == "material") is success
    if not success:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="available Action"):
            _run(
                "study_srd_maze_escape", state, actor_state="guard", maze_proficiency_factor="none"
            )
        assert state == before and actual.consumed == [(20, roll)]



def test_exile_retirement_queues_once_then_outer_settlement_is_idempotent(monkeypatch):
    state, data, params, _ = _exile_case(monkeypatch)
    _run("cast_spell", state, participant_data=data, **params)
    _run("end_srd_concentration_node", state, source_state="hero", target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["travel_exile"]["return_pending"] is True
    assert _plane(state, "guard").startswith("srd_exile:")
    _run("settle_srd_travel_exile_returns", state)
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    _run("settle_srd_travel_exile_returns", state)
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision
    assert _plane(state, "guard") == "material"



@pytest.mark.parametrize(
    "spell,defect",
    [("banishment", "range"), ("maze", "range"), ("maze", "plane"), ("banishment", "wall")],
)
def test_exile_admitted_scene_guards_precede_payment(monkeypatch, spell, defect):
    state, data, params, actual = _exile_case(monkeypatch, spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if defect == "range":
        scene["entities"][1]["position"] = [31 if spell == "banishment" else 61, 0, 0]
    elif defect == "plane":
        scene["entities"][1]["metadata"]["plane"] = "ethereal"
    else:
        scene["entities"][1]["position"] = [20, 0, 0]
        scene["geometry"] = [_wall(2)]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual caster can see"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before and actual.consumed == []



@pytest.mark.parametrize("ending", ["voluntary", "expiry"])
def test_banishment_maximum_party_is_six_actual_targets_with_individual_saves(monkeypatch, ending):
    state, data, params, actual = _exile_case(monkeypatch, "banishment", [1] * 6)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    entities = state[f"{SRD}.entities"]
    targets = ["guard"] + [f"exile-{index}" for index in range(5)]
    for index, target_id in enumerate(targets[1:], 1):
        entities[target_id] = deepcopy(entities["guard"])
        scene["entities"].append(
            _entity(
                target_id, [5, index * 2, 0], participant_ref=target_id, creature_type="humanoid"
            )
        )
    params.update(targets=targets, slot_resource="spell_slot_9")
    _run("cast_spell", state, participant_data=data, **params)
    assert actual.consumed == [(20, 1)] * 6
    assert len({_plane(state, identity) for identity in targets}) == 6
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    if ending == "expiry":
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=19)
        assert len({_plane(state, identity) for identity in targets}) == 6
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "banishment"
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    else:
        _run("end_srd_concentration_voluntarily", state, caster_state="hero")
    assert all(_plane(state, identity) == "material" for identity in targets)
    assert all(
        "incapacitated" not in state[f"{SRD}.entities"][identity]["conditions"]
        for identity in targets
    )




def test_maze_self_study_uses_actual_participant_intelligence_and_proficiency(monkeypatch):
    state, data, params, actual = _exile_case(monkeypatch, rolls=[10])
    data["skill_proficiencies"] = ["investigation"]
    params.update(targets=["hero"], target="hero")
    _run("cast_spell", state, participant_data=data, **params)
    assert _plane(state).startswith("srd_exile:") and actual.consumed == []
    _run(
        "study_srd_maze_escape",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        maze_proficiency_factor="normal",
    )
    assert state[f"{SRD}.entities"]["hero"]["last_maze_study"]["total"] == 20
    assert _plane(state) == "material" and actual.consumed == [(20, 10)]



def test_exile_return_does_not_retire_reused_other_condition_source(monkeypatch):
    state, data, params, _ = _exile_case(monkeypatch, "banishment", [1])
    _run("cast_spell", state, participant_data=data, **params)
    source_id = state[f"{SRD}.entities"]["guard"]["travel_exile"]["condition_source_id"]
    _run("retire_srd_timed_condition_source", state, lasting_state="state", source_id=source_id)
    _run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        spell="blindness_deafness",
        condition="blinded",
        save_dc=15,
        duration_rounds=100,
        repeat_save_timing="none",
    )
    replacement_id = state[f"{SRD}.entities"]["guard"]["timed_condition_source_ids"][-1]
    assert replacement_id == source_id
    _run("end_srd_concentration_voluntarily", state, caster_state="hero")
    assert state[f"{SRD}.state"]["timed_condition_sources"][replacement_id]["installed"] is True
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "incapacitated" not in state[f"{SRD}.entities"]["guard"]["conditions"]



@pytest.mark.parametrize("spell", ["banishment", "maze"])
def test_public_exile_paid_cast_reopen_actual_return_retry_and_later_movement(
    monkeypatch,
    tmp_path,
    spell,
):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, params, actual = _exile_case(
        monkeypatch,
        spell,
        [1] if spell == "banishment" else [15],
    )
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / (spell + ".sqlite3")
    cast = ActionCommand(
        campaign_id="casting",
        action_id="exile",
        idempotency_key="exile",
        owner_id="casting-owner",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="casting-turn",
        package_id=SRD,
        tool_name="cast_spell_outside_combat",
        parameters=params,
    )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, spell)
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _plane(saved.state, "guard").startswith("srd_exile:")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2
        assert mechanics.execute_action(cast) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        if spell == "maze":
            tool_name = "study_srd_maze_escape"
            parameters = {"actor_state": "guard", "maze_proficiency_factor": "none"}
        else:
            tool_name = "end_concentration"
            parameters = {"caster_state": "hero"}
        returning = replace(
            cast,
            action_id="return",
            idempotency_key="return",
            expected_revision=saved.revision,
            tool_name=tool_name,
            parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(returning)
        assert receipt.status is ActionStatus.COMMITTED
        returned = owner.snapshot("casting")
        assert _plane(returned.state, "guard") == "material"
        assert returned.state[f"{SRD}.entities"]["guard"]["travel_exile"] is None
        assert owner.execute_action(returning) == receipt
        assert owner.snapshot("casting").state == returned.state
        moving = replace(
            cast,
            action_id="walk",
            idempotency_key="walk",
            expected_revision=returned.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [5, 5, 0]},
        )
        receipt = StatelessMcpMechanics(owner).execute_action(moving)
        assert receipt.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert _point(final.state, "guard") == [5, 5, 0]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2
        assert actual.consumed == [(20, 1 if spell == "banishment" else 15)]


def _banishment_plane_case(monkeypatch, creature_type="fiend", pick=2, save=1):
    state, data, params, actual = _exile_case(monkeypatch, "banishment")
    actual.rolls = [(20, save), (2, pick)]
    state[f"{SRD}.entities"]["guard"]["creature_type"] = creature_type
    scene = state[f"{SRD}.state"]["spatial_scene"]
    orb = next(entity for entity in scene["entities"] if entity["id"] == "orb")
    orb.update(position=[100, 0, 0], movement="passable")
    orb["metadata"]["plane"] = "associated"
    other = deepcopy(orb)
    other.update(id="other-location", position=[150, 0, 0])
    scene["entities"].append(other)
    _run(
        "record_srd_banishment_plane", state, creature_type=creature_type,
        destination_subjects=["orb", "other-location"],
    )
    return state, data, params, actual



@pytest.mark.parametrize("creature_type", ["aberration", "celestial", "elemental", "fey", "fiend"])
@pytest.mark.parametrize("pick", [1, 2])
def test_banishment_associated_full_minute_actual_random_location(monkeypatch, creature_type, pick):
    state, data, params, actual = _banishment_plane_case(monkeypatch, creature_type, pick)
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=19)
    assert _plane(state, "guard").startswith("srd_exile:")
    assert actual.consumed == [(20, 1)]
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    assert _plane(state, "guard") == "associated"
    assert _point(state, "guard") == [100 if pick == 1 else 150, 0, 0]
    target = state[f"{SRD}.entities"]["guard"]
    assert target["travel_exile"] is None and "incapacitated" not in target["conditions"]
    assert target["last_banishment_arrival"]["roll"]["total"] == pick
    assert target["last_banishment_arrival"]["creature_type"] == creature_type
    assert actual.consumed == [(20, 1), (2, pick)]
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert _plane(state, "guard") == "associated" and actual.consumed == [(20, 1), (2, pick)]



def test_banishment_associated_early_end_returns_without_location_roll(monkeypatch):
    state, data, params, actual = _banishment_plane_case(monkeypatch)
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=19)
    _run("end_concentration", state, caster_state="hero")
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    assert _plane(state, "guard") == "material" and _point(state, "guard") == [5, 0, 0]
    assert actual.consumed == [(20, 1)]



@pytest.mark.parametrize("defect", ["plane", "position", "occupancy"])
def test_banishment_associated_changed_arrival_rejects_before_random_roll(monkeypatch, defect):
    state, data, params, actual = _banishment_plane_case(monkeypatch)
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=19)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    orb = next(e for e in scene["entities"] if e["id"] == "orb")
    if defect == "plane":
        orb["metadata"]["plane"] = "different"
    elif defect == "position":
        orb["position"] = [101, 0, 0]
    else:
        scene["entities"].append(_entity("occupant", [100, 0, 0], plane="associated"))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="changed|occupied"):
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    assert state == before and actual.consumed == [(20, 1)]



def test_banishment_associated_paid_grant_preserves_registered_pool(monkeypatch):
    state, data, params, actual = _banishment_plane_case(monkeypatch)
    _run("cast_spell", state, participant_data=data, **params)
    _run(
        "record_srd_banishment_plane", state, creature_type="fiend",
        destination_subjects=["other-location", "orb"],
    )
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert _point(state, "guard") == [150, 0, 0]
    assert actual.consumed == [(20, 1), (2, 2)]



@pytest.mark.parametrize("defect", ["plane", "position", "identity"])
def test_banishment_plane_registration_requires_distinct_actual_same_plane_locations(
    monkeypatch, defect
):
    state, _, _, actual = _banishment_plane_case(monkeypatch)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    other = next(e for e in scene["entities"] if e["id"] == "other-location")
    subjects = ["orb", "other-location"]
    if defect == "plane":
        other["metadata"]["plane"] = "different"
    elif defect == "position":
        other["position"] = [100, 0, 0]
    else:
        subjects = ["orb", "orb"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="distinct actual"):
        _run("record_srd_banishment_plane", state, creature_type="fiend",
             destination_subjects=subjects)
    assert state == before and actual.consumed == []



def test_banishment_associated_successful_real_save_never_rolls_arrival(monkeypatch):
    state, data, params, actual = _banishment_plane_case(monkeypatch, save=20)
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert _plane(state, "guard") == "material" and actual.consumed == [(20, 20)]



def test_banishment_multiple_associated_recipients_reject_before_payment(monkeypatch):
    state, data, params, actual = _banishment_plane_case(monkeypatch)
    entities = state[f"{SRD}.entities"]
    entities["second-fiend"] = deepcopy(entities["guard"])
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("second-fiend", [10, 0, 0], participant_ref="second-fiend", creature_type="fiend")
    )
    params.update(targets=["guard", "second-fiend"], slot_resource="spell_slot_5")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="simultaneous random arrival"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before and actual.consumed == []



def test_banishment_associated_three_locations_uses_actual_d3(monkeypatch):
    state, data, params, actual = _banishment_plane_case(monkeypatch)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    third = deepcopy(next(e for e in scene["entities"] if e["id"] == "orb"))
    third.update(id="third-location", position=[200, 0, 0])
    scene["entities"].append(third)
    _run("record_srd_banishment_plane", state, creature_type="fiend",
         destination_subjects=["orb", "other-location", "third-location"])
    actual.rolls = [(20, 1), (3, 3)]
    _run("cast_spell", state, participant_data=data, **params)
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert _point(state, "guard") == [200, 0, 0] and _plane(state, "guard") == "associated"
    assert actual.consumed == [(20, 1), (3, 3)]



def test_public_banishment_registration_paid_cast_reopen_random_arrival_and_retry(
    monkeypatch, tmp_path
):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, params, actual = _banishment_plane_case(monkeypatch)
    state[f"{SRD}.state"].pop("banishment_plane_associations")
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / "associated-banishment.sqlite3"
    registering = ActionCommand(
        campaign_id="casting", action_id="associate", idempotency_key="associate",
        owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
        turn_id="casting-turn", package_id=SRD, tool_name="record_srd_banishment_plane",
        parameters={"creature_type": "fiend", "destination_subjects": ["orb", "other-location"]},
    )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "banishment")
        mechanics = StatelessMcpMechanics(owner)
        registered_receipt = mechanics.execute_action(registering)
        assert registered_receipt.status is ActionStatus.COMMITTED
        registered = owner.snapshot("casting")
        assert mechanics.execute_action(registering) == registered_receipt
        assert owner.snapshot("casting").state == registered.state
        casting = replace(
            registering, action_id="banish", idempotency_key="banish",
            expected_revision=registered.revision, tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        cast_receipt = mechanics.execute_action(casting)
        assert cast_receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _plane(saved.state, "guard").startswith("srd_exile:")
        assert mechanics.execute_action(casting) == cast_receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        arriving = replace(
            registering, action_id="full-minute", idempotency_key="full-minute",
            expected_revision=saved.revision, tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 10},
        )
        arrival_receipt = StatelessMcpMechanics(owner).execute_action(arriving)
        assert arrival_receipt.status is ActionStatus.COMMITTED
        arrived = owner.snapshot("casting")
        assert _plane(arrived.state, "guard") == "associated"
        assert _point(arrived.state, "guard") == [150, 0, 0]
        assert arrived.state[f"{SRD}.entities"]["guard"]["travel_exile"] is None
        assert owner.execute_action(arriving) == arrival_receipt
        assert owner.snapshot("casting").state == arrived.state
        walking = replace(
            registering, action_id="later-walk", idempotency_key="later-walk",
            expected_revision=arrived.revision, tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [150, 5, 0]},
        )
        walk_receipt = StatelessMcpMechanics(owner).execute_action(walking)
        assert walk_receipt.status is ActionStatus.COMMITTED
        later = owner.snapshot("casting")
        assert _point(later.state, "guard") == [150, 5, 0]
        assert later.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert actual.consumed == [(20, 1), (2, 2)]


def _demiplane_room_case(material="stone"):
    state, data, params = _case("etherealness")
    _run("create_srd_demiplane_room", state, caster_state="hero",
         room_id="hero:demiplane:1", material=material)
    return state, data, params



def _demiplane_portal_case(material="stone", *, paid=False):
    state, data, params = _case("demiplane" if paid else "misty_step")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(10)]
    params.update(demiplane_surface_id="wall", travel_destination=[10, 0, 0],
                  demiplane_material=material)
    if paid:
        _run("cast_spell", state, participant_data=data, **params)
    else:
        _run("validate_srd_demiplane_cast", state, caster_state="hero", surface_id="wall",
             point=[10, 0, 0], face="west", room_id="", material=material)
        _run("install_srd_demiplane", state, caster_state="hero", surface_id="wall",
             point=[10, 0, 0], face="west", room_id="", material=material, slot_level=8)
    return state, data, params



def _enter_demiplane(state):
    door = state[f"{SRD}.entities"]["hero"]["last_demiplane_door"]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[9, 0, 0], mode="walk")
    _run("operate_srd_locked_object", state, actor_state="hero", object_id=door, operation="open")
    _run("traverse_srd_demiplane_door", state, actor_state="hero", door_subject=door,
         destination=[-14, 0, -15])
    return door



@pytest.mark.parametrize("material", ["wood", "stone"])
def test_demiplane_actual_door_operation_passage_budget_and_creator_room_reuse(material):
    state, _, _ = _demiplane_portal_case(material)
    door = _enter_demiplane(state)
    hero = state[f"{SRD}.entities"]["hero"]
    room = hero["last_demiplane_room"]
    assert _plane(state) == room and _point(state) == [-14, 0, -15]
    assert hero["scene_movement_spent"] == 11
    faces = [e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
             if e["metadata"].get("demiplane_door") == door]
    assert len(faces) == 2
    assert all(e["metadata"]["srd_object_access"]["closed"] is False for e in faces)
    _run("operate_srd_locked_object", state, actor_state="hero", object_id="dp_inside:1",
         operation="close")
    assert all(e["metadata"]["srd_object_access"]["closed"] is True for e in
               state[f"{SRD}.state"]["spatial_scene"]["entities"] if
               e["metadata"].get("demiplane_door") == door)
    _run("operate_srd_locked_object", state, actor_state="hero", object_id="dp_inside:1",
         operation="open")
    _run("traverse_srd_demiplane_door", state, actor_state="hero", door_subject="dp_inside:1",
         destination=[9, 0, 0])
    assert _plane(state) == "material"
    _run("end_srd_demiplane_door", state, door_id=door)
    _run("install_srd_demiplane", state, caster_state="hero", surface_id="wall",
         point=[10, 0, 0], face="west", room_id=room, material="stone", slot_level=8)
    assert state[f"{SRD}.state"]["demiplane_room_ids"] == [room]
    assert state[f"{SRD}.entities"]["hero"]["last_demiplane_door"] == "dp_door:2"
    assert next(z for z in state[f"{SRD}.state"]["spatial_scene"]["zones"]
                if z["id"] == room)["metadata"]["material"] == material



@pytest.mark.parametrize("shunt", [False, True])
def test_demiplane_exact_clock_keeps_room_objects_and_applies_optional_prone_shunt(shunt):
    state, _, _ = _demiplane_portal_case()
    door = _enter_demiplane(state)
    room = _plane(state)
    _run("move_srd_travel_subject", state, subject="orb", destination=[0, 0, -15],
         plane=room, teleport=False)
    _run("choose_srd_demiplane_shunt", state, actor_state="hero", door_subject=door, shunt=shunt)
    _run("advance_srd_lasting_time", state, elapsed_rounds=599)
    assert _plane(state) == room
    assert state[f"{SRD}.state"]["demiplane_doors"][0]["active"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _plane(state, "orb") == room
    assert _point(state, "orb") == [0, 0, -15]
    assert state[f"{SRD}.state"]["demiplane_doors"][0]["active"] is False
    assert _plane(state) == ("material" if shunt else room)
    assert _point(state) == ([9.6, 0, 0] if shunt else [-14, 0, -15])
    assert ("prone" in state[f"{SRD}.entities"]["hero"]["conditions"]) is shunt
    before = deepcopy(state)
    _run("refresh_srd_demiplane_doors", state)
    assert state == before



def test_demiplane_two_actual_shunts_choose_distinct_nearest_unoccupied_spaces():
    state, _, _ = _demiplane_portal_case()
    door = _enter_demiplane(state)
    room = _plane(state)
    _run("move_srd_travel_subject", state, subject="guard", destination=[-13, 1, -15],
         plane=room, teleport=False)
    for actor in ("hero", "guard"):
        _run("choose_srd_demiplane_shunt", state, actor_state=actor,
             door_subject=door, shunt=True)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _plane(state) == _plane(state, "guard") == "material"
    assert _point(state, "guard") == [9.6, 0, 0]
    assert _point(state) == [9.6, -0.8, 0]
    assert all("prone" in state[f"{SRD}.entities"][actor]["conditions"]
               for actor in ("hero", "guard"))
    assert state[f"{SRD}.state"]["demiplane_doors"][0]["active"] is False



@pytest.mark.parametrize("defect,pattern", [
    ("closed", "Open the actual"), ("distant", "Reach the actual source"),
    ("large", "larger squeezing"), ("occupied", "unoccupied"),
    ("outside", "immediately beyond"), ("counterpart", "counterpart face"),
])
def test_demiplane_passage_rejects_real_disqualifications_without_movement_or_cost(defect, pattern):
    state, _, _ = _demiplane_portal_case()
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[9, 0, 0], mode="walk")
    if defect != "closed":
        _run("operate_srd_locked_object", state, actor_state="hero", object_id="dp_door:1",
             operation="open")
    destination = [-14, 0, -15]
    if defect == "distant":
        _run("move_srd_travel_subject", state, subject="hero", destination=[0, 0, 0],
             plane="material", teleport=False)
    elif defect == "large":
        state[f"{SRD}.entities"]["hero"]["size_rank"] = 3
    elif defect == "occupied":
        _run("move_srd_travel_subject", state, subject="orb", destination=destination,
             plane="dp_room:1", teleport=False)
    elif defect == "outside":
        destination = [100, 0, -15]
    elif defect == "counterpart":
        next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if
             e["id"] == "dp_inside:1")["metadata"]["srd_object_access"]["closed"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=pattern):
        _run("traverse_srd_demiplane_door", state, actor_state="hero", door_subject="dp_door:1",
             destination=destination)
    assert state == before



def test_demiplane_paid_named_cast_creates_exact_room_and_spends_once():
    state, _, _ = _demiplane_portal_case("wood", paid=True)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_8"] == 2
    assert hero["last_demiplane_room"] == "dp_room:1"
    assert state[f"{SRD}.state"]["demiplane_doors"][0]["slot_level"] == 8



def test_public_demiplane_paid_door_passage_reopen_expiry_shunt_and_retry(tmp_path, monkeypatch):
    import tests.srd_full.test_srd_public_spell_casting as public

    def actual_case(_spell):
        state, data, params = _case("demiplane")
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(10)]
        params.update(
            demiplane_surface_id="wall",
            travel_destination=[10, 0, 0],
            demiplane_material="wood",
            demiplane_wall_thickness=0.5,
            demiplane_door_width=4,
            demiplane_door_height=7.5,
            demiplane_door_depth=0.5,
        )
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", actual_case)
    database = tmp_path / "demiplane.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "demiplane")
        command = ActionCommand(
            campaign_id="casting",
            action_id="demiplane",
            idempotency_key="demiplane",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        before_creation = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="actual body"):
            mechanics.execute_action(
                replace(
                    command,
                    action_id="short-aperture",
                    idempotency_key="short-aperture",
                    parameters={**params, "demiplane_door_height": 5},
                )
            )
        assert owner.snapshot("casting").state == before_creation.state
        assert owner.snapshot("casting").revision == before_creation.revision
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        created_scene = owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"]
        physical_faces = [
            row
            for row in SpatialEngine(created_scene).snapshot.as_payload()["geometry"]
            if row["metadata"].get("room_id") == "dp_room:1"
        ]
        assert len(physical_faces) == 6
        assert (
            next(row for row in physical_faces if row["metadata"]["ground"] is True)["volume"][
                "min"
            ][2]
            == -15.5
        )
        actual_door = next(row for row in created_scene["entities"] if row["id"] == "dp_door:1")
        assert actual_door["position"] == [9.75, 0, 0]
        assert actual_door["footprint"] == {"kind": "box", "half_extents": [0.25, 2]}
        assert actual_door["height"] == 7.5
        for action, tool, parameters in (
            (
                "approach",
                "move_srd_scene_creature",
                {
                    "actor_state": "hero",
                    "destination": [9, 0, 0],
                    "mode": "walk",
                },
            ),
            (
                "open",
                "operate_srd_locked_object",
                {
                    "actor_state": "hero",
                    "object_id": "dp_door:1",
                    "operation": "open",
                },
            ),
            (
                "enter",
                "traverse_srd_demiplane_door",
                {
                    "actor_state": "hero",
                    "door_subject": "dp_door:1",
                    "destination": [-14, 0, -15],
                },
            ),
            (
                "choice",
                "choose_srd_demiplane_shunt",
                {
                    "actor_state": "hero",
                    "door_subject": "dp_door:1",
                    "shunt": True,
                },
            ),
        ):
            result = mechanics.execute_action(
                replace(
                    command,
                    action_id=action,
                    idempotency_key=action,
                    expected_revision=owner.snapshot("casting").revision,
                    tool_name=tool,
                    parameters=parameters,
                )
            )
            assert result.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _plane(saved.state) == "dp_room:1"
        assert _point(saved.state) == [-14, 0, -15]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        ending = replace(
            command,
            action_id="expire",
            idempotency_key="expire",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        )
        result = mechanics.execute_action(ending)
        assert result.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(ending) == result
        after = owner.snapshot("casting")
        assert _plane(after.state) == "material" and _point(after.state) == [9.6, 0, 0]
        assert "prone" in after.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        assert after.state[f"{SRD}.state"]["demiplane_room_ids"] == ["dp_room:1"]
        assert [
            row
            for row in SpatialEngine(
                after.state[f"{SRD}.state"]["spatial_scene"]
            ).snapshot.as_payload()["geometry"]
            if row["metadata"].get("room_id") == "dp_room:1"
        ] == physical_faces
        later = mechanics.execute_action(
            replace(
                command,
                action_id="later",
                idempotency_key="later",
                expected_revision=after.revision,
                tool_name="move_srd_scene_creature",
                parameters={"actor_state": "hero", "destination": [8, 0, 0], "mode": "walk"},
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert _point(owner.snapshot("casting").state) == [8, 0, 0]



@pytest.mark.parametrize("material", ["wood", "stone"])
def test_demiplane_room_creation_admits_persistent_exact_size_and_sole_body_bounds(material):
    state, _, _ = _demiplane_room_case(material)
    result = _run(
        "read_srd_demiplane_room", state, caster_state="hero", room_id="hero:demiplane:1"
    ).result["context"]["_demiplane_room"]
    assert result["shape"] == {"kind": "box", "min": [-15, -15, -15], "max": [15, 15, 15]}
    assert result["metadata"] == {
        "kind": "srd_demiplane_room",
        "creator": "hero",
        "plane": "hero:demiplane:1",
        "material": material,
        "permanent": True,
        "wall_thickness": 0.25,
    }
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == 7
    _run(
        "move_srd_travel_subject",
        state,
        subject="hero",
        destination=[14.6, 0, 9],
        plane=result["id"],
        teleport=False,
    )
    assert _point(state) == [14.6, 0, 9]
    assert _plane(state) == result["id"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="nature and contents"):
        _run("read_srd_demiplane_room", state, caster_state="guard", room_id=result["id"])
    assert state == before



@pytest.mark.parametrize("destination", [
    [14.7, 0, 0], [-14.7, 0, 0], [0, 14.7, 0], [0, -14.7, 0],
    [0, 0, -15.1], [0, 0, 9.1],
])
def test_demiplane_room_actual_relocation_rejects_body_or_height_outside(destination):
    state, _, _ = _demiplane_room_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="inside the thirty-foot"):
        _run("move_srd_travel_subject", state, subject="hero", destination=destination,
             plane="hero:demiplane:1", teleport=False)
    assert state == before



def test_demiplane_ethereal_return_needs_nearest_space_inside_the_actual_room():
    state, data, params = _demiplane_room_case()
    _run("move_srd_travel_subject", state, subject="hero", destination=[0, 0, 0],
         plane="hero:demiplane:1", teleport=False)
    next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
         if e["id"] == "hero")["metadata"]["borders_ethereal"] = True
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"]["return_plane"] == "hero:demiplane:1"
    _run("move_srd_travel_subject", state, subject="hero", destination=[30, 0, 0],
         plane="ethereal", teleport=False)
    _run("end_srd_etherealness_target", state, target_state="hero")
    assert _point(state) == [14.6, 0, 0]
    assert _plane(state) == "hero:demiplane:1"
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 470



def test_demiplane_surface_uses_actual_visible_box_face_and_plane_defaults():
    state, _, _ = _case("misty_step")
    wall = _wall(10)
    wall["metadata"].pop("plane", None)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    result = _run("qualify_srd_demiplane_surface", state, caster_state="hero",
                  surface_id=wall["id"], point=[10, 0, 0], face="west")
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == 0
    assert _point(state) == [0, 0, 0]



@pytest.mark.parametrize("defect,pattern", [
    ("plane", "current plane"), ("face", "exact flat surface"),
    ("interior", "exact flat surface"), ("outside", "exact flat surface"),
    ("distant", "beyond 60"), ("passable", "solid box"),
    ("inactive", "solid box"), ("blind", "can see"),
])
def test_demiplane_surface_rejects_actual_nonqualifying_geometry_without_mutation(defect, pattern):
    state, _, _ = _case("misty_step")
    wall = _wall(10)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    point, face = [10, 0, 0], "west"
    if defect == "plane":
        wall["metadata"]["plane"] = "ethereal"
    elif defect == "face":
        face = "east"
    elif defect == "interior":
        point[0] = 15
    elif defect == "outside":
        point[1] = 100
    elif defect == "distant":
        wall["volume"]["min"][0] = 100
        wall["volume"]["max"][0] = 110
        point[0] = 100
    elif defect == "passable":
        wall["movement"] = "passable"
    elif defect == "inactive":
        wall["active"] = False
    else:
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=pattern):
        _run("qualify_srd_demiplane_surface", state, caster_state="hero",
             surface_id=wall["id"], point=point, face=face)
    assert state == before


def test_demiplane_room_knowledge_is_actual_actor_source_qualified_and_reversible():
    state, _, _ = _demiplane_room_case()
    room = "hero:demiplane:1"
    with pytest.raises(RulesetPreconditionRejected, match="nature and contents"):
        _run("read_srd_demiplane_room", state, caster_state="guard", room_id=room)
    _run("create_srd_demiplane_room", state, caster_state="guard",
         room_id="guard:demiplane:2", material="wood")
    for known in ("guard:demiplane:2", room, room):
        _run("record_srd_demiplane_room_knowledge", state, actor_state="guard",
             room_id=known, nature_and_contents_known=True)
    assert state[f"{SRD}.entities"]["guard"]["demiplane_known_rooms"] == [
        "guard:demiplane:2", room,
    ]
    actual = _run("read_srd_demiplane_room", state, caster_state="guard", room_id=room)
    assert actual.result["context"]["_demiplane_room"]["metadata"]["creator"] == "hero"
    assert "demiplane_known_rooms" not in state[f"{SRD}.entities"]["hero"]
    _run("record_srd_demiplane_room_knowledge", state, actor_state="guard",
         room_id=room, nature_and_contents_known=False)
    assert state[f"{SRD}.entities"]["guard"]["demiplane_known_rooms"] == ["guard:demiplane:2"]
    with pytest.raises(RulesetPreconditionRejected, match="nature and contents"):
        _run("read_srd_demiplane_room", state, caster_state="guard", room_id=room)
    _run("read_srd_demiplane_room", state, caster_state="guard", room_id="guard:demiplane:2")



@pytest.mark.parametrize("room", ["absent", "ambient_material", "hero:demiplane:1"])
def test_demiplane_room_knowledge_rejects_unregistered_or_changed_source_without_writing(room):
    state, _, _ = _demiplane_room_case()
    if room == "hero:demiplane:1":
        actual = next(zone for zone in state[f"{SRD}.state"]["spatial_scene"]["zones"]
                      if zone["id"] == room)
        actual["metadata"]["kind"] = "ordinary_room"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("record_srd_demiplane_room_knowledge", state, actor_state="guard",
             room_id=room, nature_and_contents_known=True)
    assert state == before



def test_demiplane_unknown_room_rejects_before_actual_slot_then_known_room_is_reused():
    state, data, params = _case("demiplane")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(10)]
    _run("create_srd_demiplane_room", state, caster_state="guard",
         room_id="guard:demiplane:1", material="wood")
    params.update(demiplane_surface_id="wall", travel_destination=[10, 0, 0],
                  demiplane_room="guard:demiplane:1", demiplane_material="stone")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="nature and contents"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    _run("record_srd_demiplane_room_knowledge", state, actor_state="hero",
         room_id="guard:demiplane:1", nature_and_contents_known=True)
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    assert state[f"{SRD}.entities"]["hero"]["last_demiplane_room"] == "guard:demiplane:1"
    assert state[f"{SRD}.state"]["demiplane_room_ids"] == ["guard:demiplane:1"]
    room = next(zone for zone in state[f"{SRD}.state"]["spatial_scene"]["zones"]
                if zone["id"] == "guard:demiplane:1")
    assert room["metadata"]["material"] == "wood" and room["metadata"]["creator"] == "guard"



def test_public_demiplane_room_knowledge_reopens_for_an_independent_paid_npc_cast(
    tmp_path,
    monkeypatch,
):
    import tests.srd_full.test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

    def actual_case(_spell):
        state, data, params = _case("demiplane")
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["geometry"] = [_wall(10)]
        scene["entities"].append(_entity("mage", [0, 5, 0], creature_type="humanoid"))
        params.update(
            demiplane_surface_id="wall", travel_destination=[10, 0, 0], demiplane_material="wood"
        )
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", actual_case)
    database = tmp_path / "demiplane-knowledge.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "demiplane")
        creator, creator_command = _act(owner, "cast_spell_outside_combat", **params)
        assert creator.status is ActionStatus.COMMITTED
        _act(
            owner, "move_srd_scene_creature", actor_state="hero", destination=[9, 0, 0], mode="walk"
        )
        _act(
            owner,
            "operate_srd_locked_object",
            actor_state="hero",
            object_id="dp_door:1",
            operation="open",
        )
        prepared, _ = _prepare_mage(
            owner,
            [
                {"spell": "demiplane", "uses_per_day": 2, "casting_level": 8},
            ],
        )
        assert prepared.status is ActionStatus.COMMITTED
        params.pop("caster")
        params.pop("slot_resource")
        params.update(
            caster_state="mage",
            demiplane_room="dp_room:1",
            travel_destination=[10, 5, 0],
            demiplane_material="stone",
        )
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="nature and contents"):
            _act(owner, "cast_spell_outside_combat", **params)
        assert owner.snapshot("casting").state == before.state
        knowledge, knowledge_command = _act(
            owner,
            "record_srd_demiplane_room_knowledge",
            actor_state="mage",
            room_id="dp_room:1",
            nature_and_contents_known=True,
        )
        assert knowledge.status is ActionStatus.COMMITTED
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(knowledge_command) == knowledge
        schema = next(
            tool.input_schema
            for tool in mechanics.list_tools("casting")
            if tool.name == "record_srd_demiplane_room_knowledge"
        )
        assert set(schema["properties"]) == {"actor_state", "room_id", "nature_and_contents_known"}
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(creator_command) == creator
        assert mechanics.execute_action(knowledge_command) == knowledge
        reused, reuse_command = _act(owner, "cast_spell_outside_combat", **params)
        assert reused.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        entities = after.state[f"{SRD}.entities"]
        assert entities["hero"]["resources"]["spell_slot_8"] == 2
        assert entities["mage"]["spell_uses_remaining"]["demiplane"] == 1
        assert entities["mage"]["last_demiplane_room"] == "dp_room:1"
        assert entities["mage"]["last_demiplane_door"] == "dp_door:2"
        assert after.state[f"{SRD}.state"]["demiplane_room_ids"] == ["dp_room:1"]
        actual = next(
            zone
            for zone in after.state[f"{SRD}.state"]["spatial_scene"]["zones"]
            if zone["id"] == "dp_room:1"
        )
        assert actual["metadata"]["creator"] == "hero" and actual["metadata"]["material"] == "wood"
        assert mechanics.execute_action(reuse_command) == reused
        revoked, _ = _act(
            owner,
            "record_srd_demiplane_room_knowledge",
            actor_state="mage",
            room_id="dp_room:1",
            nature_and_contents_known=False,
        )
        assert revoked.status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="nature and contents"):
            _act(owner, "cast_spell_outside_combat", **params)
        assert owner.snapshot("casting").state == before.state
        assert before.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["demiplane"] == 1


@pytest.mark.parametrize("material,thickness", [("stone", 0.25), ("wood", 0.75)])
def test_demiplane_physical_faces_are_actual_solids_support_and_persist_after_door_end(
    material, thickness
):
    state, _, _ = _case("misty_step")
    _run(
        "create_srd_demiplane_room",
        state,
        caster_state="hero",
        room_id="physical-room",
        material=material,
        wall_thickness=thickness,
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    faces = scene["geometry"]
    assert {face["id"] for face in faces} == {
        "physical-room:" + name for name in ("west", "east", "south", "north", "floor", "ceiling")
    }
    assert all(
        face["active"] is True
        and face["movement"] == "blocking"
        and face["visibility"] == "opaque"
        and face["metadata"]["material"] == material
        and face["metadata"]["plane"] == "physical-room"
        for face in faces
    )
    floor = next(face for face in faces if face["metadata"]["ground"] is True)
    assert floor["volume"] == {
        "kind": "box",
        "min": [-15 - thickness, -15 - thickness, -15 - thickness],
        "max": [15 + thickness, 15 + thickness, -15],
    }
    _run("qualify_srd_environment_ground", state, point=[0, 0, -15], plane="physical-room")
    wall_hit = (
        SpatialEngine(scene)
        .query(
            {
                "kind": "ray",
                "map_id": "information",
                "origin": [0, 0, 0],
                "destination": [20, 0, 0],
                "include": ["geometry"],
                "max_intersections": 8,
            }
        )
        .as_payload()["intersections"][0]
    )
    assert wall_hit["id"] == "physical-room:east" and wall_hit["point"] == [15, 0, 0]
    assert scene["zones"][3]["shape"] == {
        "kind": "box",
        "min": [-15, -15, -15],
        "max": [15, 15, 15],
    }
    before = deepcopy(faces)
    _run("advance_srd_lasting_time", state, elapsed_rounds=601)
    assert state[f"{SRD}.state"]["spatial_scene"]["geometry"] == before



@pytest.mark.parametrize(
    "choices,pattern",
    [
        ({"door_width": 0.5}, "actual body"),
        ({"door_height": 5}, "actual body"),
        ({"door_width": 21}, "supporting face"),
        ({"door_height": 11}, "supporting face"),
        ({"door_height": 31}, "thirty-foot"),
    ],
)
def test_demiplane_physical_creation_intent_rejects_actual_body_or_surface_before_payment(
    choices, pattern
):
    state, _, _ = _case("demiplane")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(10)]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=pattern):
        _run(
            "validate_srd_demiplane_cast",
            state,
            caster_state="hero",
            surface_id="wall",
            point=[10, 0, 0],
            face="west",
            room_id="",
            material="stone",
            **choices,
        )
    assert state == before



def test_demiplane_physical_exterior_needs_real_map_space_before_payment():
    state, _, _ = _case("demiplane")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1)]
    actual_map = state[f"{SRD}.state"]["spatial_scene"]["map"]
    actual_map["bounds"] = {"min": [-30000, -30000, -15], "max": [30000, 30000, 15]}
    actual_map["layers"] = [{"id": "world", "min_elevation": -15, "max_elevation": 15}]
    for zone in state[f"{SRD}.state"]["spatial_scene"]["zones"]:
        zone["shape"]["min"][2], zone["shape"]["max"][2] = -15, 15
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="thirty-foot"):
        _run(
            "validate_srd_demiplane_cast",
            state,
            caster_state="hero",
            surface_id="wall",
            point=[10, 0, 0],
            face="west",
            room_id="",
            material="stone",
        )
    assert state == before



def test_demiplane_physical_panels_match_intent_and_later_actual_medium_body_must_fit():
    state, _, _ = _demiplane_portal_case()
    room = state[f"{SRD}.entities"]["hero"]["last_demiplane_room"]
    door = state[f"{SRD}.entities"]["hero"]["last_demiplane_door"]
    actual = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == door
    )
    assert actual["position"] == [9.875, 0, 0]
    assert actual["footprint"] == {"kind": "box", "half_extents": [0.125, 2.5]}
    assert actual["height"] == 8
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[9, 0, 0], mode="walk")
    _run("operate_srd_locked_object", state, actor_state="hero", object_id=door, operation="open")
    next(row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "hero")[
        "height"
    ] = 9
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual body"):
        _run(
            "traverse_srd_demiplane_door",
            state,
            actor_state="hero",
            door_subject=door,
            destination=[-14, 0, -15],
        )
    assert state == before and _plane(state) != room



@pytest.mark.parametrize("plane,blocked", [("material", True), ("ethereal", False)])
def test_demiplane_physical_panel_creation_reads_actual_occupancy_and_plane(plane, blocked):
    state, _, _ = _case("demiplane")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [_wall(10)]
    guard = next(row for row in scene["entities"] if row["id"] == "guard")
    guard["position"] = [9.5, 0, 0]
    guard["metadata"]["plane"] = plane
    before = deepcopy(state)
    if blocked:
        with pytest.raises(RulesetPreconditionRejected, match="occupies"):
            _run(
                "validate_srd_demiplane_cast",
                state,
                caster_state="hero",
                surface_id="wall",
                point=[10, 0, 0],
                face="west",
                room_id="",
                material="stone",
            )
    else:
        _run(
            "validate_srd_demiplane_cast",
            state,
            caster_state="hero",
            surface_id="wall",
            point=[10, 0, 0],
            face="west",
            room_id="",
            material="stone",
        )
    assert state == before



@pytest.mark.parametrize("collision", ["dp_door:1", "dp_room:1", "dp_room:1:floor"])
def test_demiplane_physical_source_ids_reject_collision_before_payment(collision):
    state, _, _ = _case("demiplane")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [_wall(10)]
    scene["entities"].append(_entity(collision, [100, 0, 0], is_object=True))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="identity is already"):
        _run(
            "validate_srd_demiplane_cast",
            state,
            caster_state="hero",
            surface_id="wall",
            point=[10, 0, 0],
            face="west",
            room_id="",
            material="stone",
        )
    assert state == before



def test_demiplane_physical_reuse_requires_actual_unoccupied_inner_panel_before_payment():
    state, _, _ = _demiplane_portal_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="occupies"):
        _run(
            "validate_srd_demiplane_cast",
            state,
            caster_state="hero",
            surface_id="wall",
            point=[10, 5, 0],
            face="west",
            room_id="dp_room:1",
            material="stone",
        )
    assert state == before
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[9, 0, 0], mode="walk")
    _run(
        "operate_srd_locked_object",
        state,
        actor_state="hero",
        object_id="dp_door:1",
        operation="open",
    )
    _run(
        "validate_srd_demiplane_cast",
        state,
        caster_state="hero",
        surface_id="wall",
        point=[10, 5, 0],
        face="west",
        room_id="dp_room:1",
        material="stone",
    )



@pytest.mark.parametrize(
    "face,point,caster,origin,extent",
    [
        ("west", [10, 0, 0], [0, 0, 0], [9.875, 0, 0], [0.25, 5, 8]),
        ("east", [20, 0, 0], [25, 0, 0], [20.125, 0, 0], [0.25, 5, 8]),
        ("north", [15, 10, 0], [15, 15, 0], [15, 10.125, 0], [5, 0.25, 8]),
        ("south", [15, -10, 0], [15, -15, 0], [15, -10.125, 0], [5, 0.25, 8]),
        ("up", [15, 0, 10], [15, 0, 15], [15, 0, 10], [5, 8, 0.25]),
        ("down", [15, 0, -10], [15, 0, -20], [15, 0, -10.25], [5, 8, 0.25]),
    ],
)
def test_demiplane_physical_face_orientation_derives_actual_panel_body(
    face, point, caster, origin, extent
):
    state, _, _ = _case("demiplane")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [_wall(10)]
    next(row for row in scene["entities"] if row["id"] == "hero")["position"] = caster
    _run(
        "validate_srd_demiplane_cast",
        state,
        caster_state="hero",
        surface_id="wall",
        point=point,
        face=face,
        room_id="",
        material="stone",
    )
    _run(
        "install_srd_demiplane",
        state,
        caster_state="hero",
        surface_id="wall",
        point=point,
        face=face,
        room_id="",
        material="stone",
        slot_level=8,
    )
    panel = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "dp_door:1"
    )
    assert panel["position"] == origin
    assert panel["footprint"] == {"kind": "box", "half_extents": [extent[0] * 0.5, extent[1] * 0.5]}
    assert panel["height"] == extent[2]



@pytest.mark.parametrize("defect", ["dimensions", "permanent"])
def test_demiplane_physical_room_resolution_requires_printed_size_and_persistence(defect):
    state, _, _ = _demiplane_room_case()
    room = next(
        zone
        for zone in state[f"{SRD}.state"]["spatial_scene"]["zones"]
        if zone["id"] == "hero:demiplane:1"
    )
    if defect == "dimensions":
        room["shape"]["max"][0] = 14
    else:
        room["metadata"]["permanent"] = False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual persistent"):
        _run("read_srd_demiplane_room", state, caster_state="hero", room_id=room["id"])
    assert state == before


def test_demiplane_shunt_uses_actual_full_former_aperture_before_anchor_distance():
    state, _, _ = _demiplane_portal_case()
    door = _enter_demiplane(state)
    _run("move_srd_travel_subject", state, subject="guard", destination=[50, 0, 0],
         plane="material", teleport=False)
    _run("choose_srd_demiplane_shunt", state, actor_state="hero", door_subject=door, shunt=True)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append({
        **_wall(5), "id": "later-solid",
        "volume": {"kind": "box", "min": [5, -5, -10], "max": [10, 5, 20]},
    })
    panel = next(entity for entity in scene["entities"] if entity["id"] == door)
    old_point = _placement(scene, panel["position"])
    assert old_point["position"] == [4.6, 0, 0]
    ending = _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    # Above the actual ten-foot wall is closer to the eight-foot aperture than the side exit.
    assert _point(state) == [10.4, 0, 10] and _plane(state) == "material"
    query = next(item["query"] for item in ending.result["spatial_evidence"]
                 if item.get("query", {}).get("reference_face") is not None)
    assert query["reference_face"] == {
        "target": {"kind": "entity", "id": door}, "face": "east",
    }
    assert "prone" in state[f"{SRD}.entities"]["hero"]["conditions"]
    panel = next(entity for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
                 if entity["id"] == door)
    assert panel["active"] is False and panel["footprint"]["half_extents"][1] == 2.5
    before = deepcopy(state)
    _run("refresh_srd_demiplane_doors", state)
    assert state == before
