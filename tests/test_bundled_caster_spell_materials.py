"""Bundled casters can cast each prepared Material spell from their shipped starting state."""

import json
from copy import deepcopy
from importlib.resources import files

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_spell_result_cards import SRD, _casting_headers, _spell_case

RECORD_SUPPLIES = "Record the carried material supplies before casting this spell."
NOT_CARRIED = "The selected Material supply is not carried"

# Rightful refusals from shipped starting equipment, by class and spell. SRD 5.2.1 Magic
# Initiate grants no Spellcasting Focus, so a caster carries the unpriced component itself
# for such a spell (moss for Light, lodestones for Mending, dirt for Longstrider). Only a
# priced, consumed component the character does not own stays refused.
EXPECTED_REFUSALS = {
    ("warlock", "protection_from_evil_and_good"): (
        "consumes Holy Water worth 25+ GP, which is not starting equipment"
    ),
}

# Mending takes 1 minute, so it goes through the longer-casting tools.
LONGER_CASTINGS = {"mending"}
BEGIN_PARAMETERS = (
    "spell", "caster", "caster_state", "slot_resource", "verbal_component_available",
    "material_component_available", "material_item_ids",
)

EXTRA_PARAMETERS = {
    "bless": {"targets": ["guard", "other"]},
    "ice_knife": {"targets": ["guard", "other"]},
    "light": {"illumination_object": "torch", "illumination_color": "white"},
    "longstrider": {"targets": ["guard"]},
    "mage_armor": {"target": "hero"},
}

# Light needs a touched object; the caster holds one in the scene.
TORCH = {
    "id": "torch", "map_id": "fixture", "position": [0.5, 0, 0],
    "footprint": {"kind": "box", "half_extents": [0.2, 0.2]}, "height": 1,
    "facing": [1, 0], "active": True, "movement": "passable", "visibility": "clear",
    "metadata": {"is_object": True, "size_rank": 0, "carried_by": "hero", "worn_by": "",
                 "plane": "material", "lighting": "bright"},
}


def _bundled_casters():
    """Every bundled starter and prepared-campaign character that prepares a Material spell."""
    campaign = files("sylvia_gm.campaign")
    rows = [
        (f"starter-{row['id']}", row["participant"], row["entity_state"])
        for row in json.loads(campaign.joinpath("srd-starters.json").read_text(encoding="utf-8"))
    ]
    ready = json.loads(campaign.joinpath("ready-campaigns.json").read_text(encoding="utf-8"))
    seen = set()
    for plan in ready["campaigns"]:
        for member in plan["party"]:
            character = member.get("character")
            if character and member["content_sha256"] not in seen:
                seen.add(member["content_sha256"])
                rows.append(
                    (character["id"], character["participant"], character["entity_state"])
                )
    headers = _casting_headers()
    return [
        (label, participant, state, spell)
        for label, participant, state in rows
        for spell in state.get("prepared_spells", [])
        if "material" in headers[spell]["components"]
    ]


CASES = _bundled_casters()


def _cast_from(participant, starting_state, spell, *, supplies=True, begin=False):
    """Cast with the character's own data over the shared spell fixture's scene and targets."""
    state, data, parameters = _spell_case(spell)
    data.update(deepcopy(participant))
    hero = state[f"{SRD}.entities"]["hero"]
    for key in ("inventory", "prepared_spells", "resources"):
        hero[key] = deepcopy(starting_state[key])
    hero.pop("spell_materials", None)
    if supplies:
        hero["spell_materials"] = deepcopy(starting_state.get("spell_materials", []))
    parameters["material_item_ids"] = [row["id"] for row in hero.get("spell_materials", [])]
    parameters.update(EXTRA_PARAMETERS.get(spell, {}))
    if spell == "light":
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(deepcopy(TORCH))
    if begin:
        state[f"{SRD}.combat"].update(
            encounter_active=False, turn_active=False, active_actor=None, phase="exploration",
        )
        parameters = {key: parameters[key] for key in BEGIN_PARAMETERS}
        return _run("begin_srd_spell_cast", state, participant_data=data, **parameters)
    return _run("cast_spell", state, participant_data=data, **parameters)


def test_every_bundled_caster_and_material_spell_is_covered():
    labels = {label for label, *_ in CASES}
    for caster in ("bard", "cleric", "druid", "paladin", "ranger", "sorcerer", "warlock", "wizard"):
        assert f"starter-{caster}" in labels
    assert {"ready-cleric-1", "ready-cleric-2", "ready-bard-1"} <= labels
    classes = {(participant["classes"][0]["id"], spell) for _, participant, _, spell in CASES}
    assert set(EXPECTED_REFUSALS) <= classes


@pytest.mark.parametrize(
    "label,participant,starting_state,spell",
    CASES,
    ids=[f"{label}-{spell}" for label, _, _, spell in CASES],
)
def test_prepared_material_spell_is_not_refused_for_missing_supplies(
    label, participant, starting_state, spell
):
    with pytest.raises(RulesetPreconditionRejected, match=RECORD_SUPPLIES):
        _cast_from(participant, starting_state, spell, supplies=False)

    refusal = EXPECTED_REFUSALS.get((participant["classes"][0]["id"], spell))
    if refusal:
        with pytest.raises(RulesetPreconditionRejected, match=NOT_CARRIED):
            _cast_from(participant, starting_state, spell)
    elif spell in LONGER_CASTINGS:
        # A casting longer than one action is begun, not resolved at once; beginning it is
        # where its Material supply is checked.
        with pytest.raises(RulesetPreconditionRejected, match=RECORD_SUPPLIES):
            _cast_from(participant, starting_state, spell, supplies=False, begin=True)
        _cast_from(participant, starting_state, spell, begin=True)
    else:
        assert _cast_from(participant, starting_state, spell).result["status"] == "committed"
