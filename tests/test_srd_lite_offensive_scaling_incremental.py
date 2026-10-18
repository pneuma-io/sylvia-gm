"""Public scaling proofs for the approved SRD thresholds 5, 11 and 17.

NPCs are explicit synthetic GM adjudications created with prepare_srd_creature.
The multiclass PC is an imported casting fixture, not complete creation or
progression proof. No dice, consumer, saved grant or casting profile is replaced
after campaign creation. The parent owns native execution on exact package bytes.
"""

import math
import re
from collections import defaultdict
from copy import deepcopy

from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID


def _retain(case, history, receipt, command):
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot() == saved
    history.append((receipt, command))


def _reopen_and_replay(case, history):
    """Old commands must retain their actual dice/cards and the entire later save."""
    final = case.snapshot()
    for receipt, command in history:
        assert case.execute(command) == receipt and case.snapshot() == final
    case.reopen()
    assert case.snapshot() == final
    for receipt, command in history:
        assert case.execute(command) == receipt and case.snapshot() == final


def _cast_card(receipt, title, *, npc):
    card = receipt.result["result_card"]
    assert card["title"] == title
    summary = {row["label"]: row["value"] for row in card["summary"]}
    if npc:
        assert summary["Casting"] == "At will" and str(summary["Spell level"]) == "0"
        assert not any(row["label"] == "Uses spent"
                       for row in [*card["summary"], *card.get("details", [])])
    else:
        assert str(summary["Spell slots spent"]) == "0"
    return card


def _damage_card(card, faces):
    """Read the real target calculation, including its recorded ordered dice."""
    matches = [re.search(r"Damage dice (\d+(?:, \d+)*); rolled (\d+)", row["value"])
               for row in card["details"]]
    matches = [match for match in matches if match]
    assert len(matches) == 1, card
    assert [int(value.strip()) for value in matches[0][1].split(",")] == list(faces)
    assert int(matches[0][2]) == sum(faces)


def _prepare(case, identity, level, spells, history):
    receipt, command = case.act(
        "prepare_srd_creature", creature_id=identity, name=identity,
        source=(f"Explicit synthetic GM-adjudicated scaling-{level} humanoid. "
                "AC 10, HP 1000, Speed 30, Initiative +0, Constitution save +0; "
                "Intelligence casting modifier +4, attack +30, save DC 30. "
                "The supplied level is this fixture's expressly granted cantrip "
                "scaling, not Challenge Rating. Listed cantrips are at will. "
                "No printed stat block, player creation or progression is claimed."),
        armor_class=10, hit_points=1000, speed=30, size_rank=1,
        initiative_modifier=0, creature_type="humanoid", constitution_save=0,
        spellcasting_ability="intelligence", spellcasting_modifier=4,
        spell_attack_bonus=30, spell_save_dc=30, cantrip_scaling_level=level,
        spell_component_exemptions=[],
        spells=[{"spell": spell, "uses_per_day": 0, "casting_level": 0}
                for spell in spells],
    )
    prepared = case.entities()[identity]
    assert prepared["spellcasting_profile"]["scaling_level"] == level
    assert prepared["spellcasting_profile"]["ability"] == "intelligence"
    for spell in spells:
        assert prepared["spell_definitions"][spell] == {
            "casting_level": 0, "uses_per_day": 0,
        }
        assert prepared["spell_uses_remaining"][spell] == 0
    _retain(case, history, receipt, command)


def _npc_choices(case, identity, spell, distance):
    choices = {key: value for key, value in case.parameters.items()
               if key not in ("caster", "slot_resource")}
    choices.update(spell=spell, caster_state=identity, target="guard",
                   target_distance=distance, casting_mode="ordinary",
                   material_component_available=True, material_item_ids=[])
    return choices


def _fire_hit(case, identity, level, dice, distance, history):
    """A natural-one miss remains a genuine paid action, with no forced reroll."""
    resources = deepcopy(case.entities()[identity]["resources"])
    hero_resources = deepcopy(case.entities()["hero"]["resources"])
    for _ in range(8):
        hp = case.entities()["guard"]["hp"]
        receipt, command = case.act(
            "cast_spell_outside_combat",
            **_npc_choices(case, identity, "fire_bolt", distance),
        )
        context = receipt.result["context"]
        assert context["_casting_profile"]["scaling_level"] == level
        assert context["_spell_attack"]["results"]
        card = _cast_card(receipt, "Fire Bolt", npc=True)
        assert case.entities()[identity]["resources"] == resources
        assert case.entities()[identity]["spell_uses_remaining"]["fire_bolt"] == 0
        assert case.entities()["hero"]["resources"] == hero_resources
        _retain(case, history, receipt, command)
        if context["_spell_attack_outcome"] == "success":
            faces = context["_spell_damage"]["results"]
            critical = context["_spell_attack_kind"] == "critical_hit"
            assert len(faces) == dice * (2 if critical else 1), (level, context)
            assert all(1 <= face <= 10 for face in faces)
            assert context["_spell_damage_amount"] == sum(faces)
            assert case.entities()["guard"]["hp"] == hp - sum(faces)
            _damage_card(card, faces)
            return
        assert context["_spell_attack_outcome"] == "failure"
        assert case.entities()["guard"]["hp"] == hp
        # Advance actual free time before another lawful at-will attempt.
        elapsed, elapsed_command = case.act("advance_srd_lasting_time", elapsed_rounds=1)
        _retain(case, history, elapsed, elapsed_command)
    raise AssertionError("Eight lawful casts produced no actual Fire Bolt hit.")


def test_offensive_scaling_level_ten_uses_srd_two_dice(tmp_path):
    """Focused RED: level ten is below the SRD third tier at eleven."""
    case = Case.__new__(Case)
    try:
        case.__init__(
            tmp_path / "offensive-srd-level-ten.db", "true_strike",
            positions={"hero": [-40, 0, 0], "guard": [10, 0, 0],
                       "other": [-50, 20, 0], "extra": [-50, -20, 0],
                       "scale10": [0, 0, 0]},
        )
        history = []
        participant = next(row for row in case.snapshot().participants
                           if row.participant_id == "hero")
        _prepare(case, "scale10", 10, ["fire_bolt"], history)
        _fire_hit(case, "scale10", 10, 2, 10, history)
        _reopen_and_replay(case, history)
        assert next(row for row in case.snapshot().participants
                    if row.participant_id == "hero") == participant
    finally:
        if getattr(case, "manager", None):
            archive_closed_case(case)


def test_offensive_scaling_srd_boundaries_and_high_level_save_admission(tmp_path):
    """One install exercises attack thresholds and the save owner above twelve."""
    boundaries = ((4, 1), (5, 2), (11, 3), (16, 3), (17, 4), (20, 4))
    positions = {"hero": [-40, 0, 0], "guard": [10, 0, 0],
                 "other": [-50, 20, 0], "extra": [-50, -20, 0]}
    positions.update({f"scale{level}": [0, 5 * index, 0]
                      for index, (level, _) in enumerate(boundaries, 1)})
    positions["save13"] = [0, 35, 0]
    case = Case.__new__(Case)
    try:
        case.__init__(
            tmp_path / "offensive-srd-boundaries.db", "true_strike", positions=positions,
            entity_changes={"guard": {"saving_throw_modifiers": {
                "strength": 0, "dexterity": -20, "constitution": 0,
                "intelligence": 0, "wisdom": 0, "charisma": 0,
            }}},
        )
        history = []
        participants = case.snapshot().participants
        # Every declared nearby creature needs its actual public state before
        # the retained scene threat reader can resolve it during any cast.
        for level, _ in boundaries:
            _prepare(case, f"scale{level}", level, ["fire_bolt"], history)
        _prepare(case, "save13", 13, ["sacred_flame"], history)
        for level, dice in boundaries:
            identity = f"scale{level}"
            distance = math.dist(positions[identity], positions["guard"])
            _fire_hit(case, identity, level, dice, distance, history)
        npc_resources = deepcopy(case.entities()["save13"]["resources"])
        hero_resources = deepcopy(case.entities()["hero"]["resources"])
        hp = case.entities()["guard"]["hp"]
        receipt, command = case.act(
            "cast_spell_outside_combat", **_npc_choices(
                case, "save13", "sacred_flame",
                math.dist(positions["save13"], positions["guard"]),
            ),
        )
        context = receipt.result["context"]
        assert context["_casting_profile"]["scaling_level"] == 13
        assert context["_spell_save"]["results"]
        assert context["_spell_save_outcome"] == "failure"
        faces = context["_spell_damage"]["results"]
        assert len(faces) == 3 and all(1 <= face <= 8 for face in faces)
        assert context["_spell_damage_amount"] == sum(faces)
        assert case.entities()["guard"]["hp"] == hp - sum(faces)
        _damage_card(_cast_card(receipt, "Sacred Flame", npc=True), faces)
        assert case.entities()["save13"]["resources"] == npc_resources
        assert case.entities()["save13"]["spell_uses_remaining"]["sacred_flame"] == 0
        assert case.entities()["hero"]["resources"] == hero_resources
        _retain(case, history, receipt, command)
        _reopen_and_replay(case, history)
        assert case.snapshot().participants == participants
    finally:
        if getattr(case, "manager", None):
            archive_closed_case(case)


def test_offensive_scaling_imported_multiclass_total_twenty_four_beams(tmp_path):
    """Warlock one / Fighter nineteen scales by total level, without creation claims."""
    case = Case.__new__(Case)
    try:
        case.__init__(
            tmp_path / "offensive-srd-multiclass-twenty.db", "true_strike",
            positions={"hero": [0, 0, 0], "guard": [20, 0, 0],
                       "other": [0, 20, 0], "extra": [-20, 0, 0]},
            participant_changes={
                "level": 20, "classes": [{"id": "warlock", "level": 1},
                                           {"id": "fighter", "level": 19}],
                "subclasses": {"fighter": "champion"}, "wizard_spellbook": [],
                "origin_feats": ["savage_attacker"],
                "saving_throw_proficiencies": ["wisdom", "charisma"],
                "abilities": {"strength": 16, "dexterity": 14, "constitution": 14,
                              "intelligence": 10, "wisdom": 10, "charisma": 18},
                "spellcasting_grants": [{"source": "warlock", "ability": "charisma",
                                         "spells": ["eldritch_blast"]}],
            },
            entity_changes={"hero": {"prepared_spells": ["eldritch_blast"],
                                      "fighter_level": 19,
                                      "resources": {"spell_slot_1": 1, "action_surge": 1,
                                                    "second_wind": 3},
                                      "resource_maxima": {"spell_slot_1": 1, "action_surge": 1,
                                                          "second_wind": 3}},
                            "guard": {"dodging": False},
                            "other": {"dodging": False}},
        )
        participant = next(row for row in case.snapshot().participants
                           if row.participant_id == "hero")
        choices = {key: value for key, value in case.parameters.items()
                   if key not in ("target", "target_distance", "caster_can_see_target")}
        choices.update(spell="eldritch_blast", slot_resource="cantrip",
                       attack_targets=["guard", "other", "guard", "other"],
                       targets_max_distance=20)
        case.start()
        before = case.snapshot()
        for allocation in (["guard"] * 3, ["guard"] * 5):
            case.reject("cast_spell", choices | {"attack_targets": allocation})
            assert case.snapshot() == before
        resources = deepcopy(case.entities()["hero"]["resources"])
        hp = {target: case.entities()[target]["hp"] for target in ("guard", "other")}
        receipt, command = case.act("cast_spell", **choices)
        assert receipt.result["context"]["_casting_profile"]["scaling_level"] == 20
        card = _cast_card(receipt, "Eldritch Blast", npc=False)
        rows = [row for row in card["details"]
                if re.search(r"\. D20 \d+(?:, \d+)*; selected ", row["value"])]
        assert len(rows) == 4, card
        losses = defaultdict(int)
        for row, target in zip(rows, choices["attack_targets"]):
            assert row["label"] == case.entities()[target]["name"]
            damage = re.search(r"Damage (\d+) \(HP (\d+); temporary HP (\d+)\)", row["value"])
            assert damage, row
            amount, hp_loss, temp_loss = map(int, damage.groups())
            assert amount == hp_loss and temp_loss == 0
            losses[target] += amount
            rolled = re.search(r"Damage dice (\d+(?:, \d+)*); rolled (\d+)", row["value"])
            if row["value"].startswith("Miss."):
                assert amount == 0 and rolled is None
            else:
                assert rolled, row
                faces = [int(value.strip()) for value in rolled[1].split(",")]
                assert len(faces) == (2 if row["value"].startswith("Critical hit.") else 1)
                assert all(1 <= face <= 10 for face in faces)
                assert sum(faces) == int(rolled[2]) == amount
        for target in hp:
            assert case.entities()[target]["hp"] == hp[target] - losses[target]
        assert case.entities()["hero"]["resources"] == resources
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is False
        history = []
        _retain(case, history, receipt, command)
        case.next_turn()
        case.next_turn()
        assert case.snapshot().state[VARIANT_ID + ".combat"]["action_available"] is True
        _reopen_and_replay(case, history)
        assert next(row for row in case.snapshot().participants
                    if row.participant_id == "hero") == participant
        assert participant.data["level"] == 20
    finally:
        if getattr(case, "manager", None):
            archive_closed_case(case)
