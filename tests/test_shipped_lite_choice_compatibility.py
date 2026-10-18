"""Preserved shipped choices: prove usable tiers and identify exact incompatible tiers."""
from copy import deepcopy
import pytest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_library_selection import materialize_library_party
from sylvia_gm.application.campaign_library_selection import resolve_campaign_library_selection
from sylvia_gm.application.reusable_participants import save_reusable_participant
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_reusable_participants import _participant, _plan_participant_authority, _starting_state
from tests.test_srd_spell_grants import _validate
from tests.test_srd_spell_result_cards import _spell_case

@pytest.mark.parametrize("source,species,spells", [
    ("tiefling_infernal", "tiefling", ["fire_bolt", "thaumaturgy"]),
    ("elf_high", "elf", ["fire_bolt"]),
    ("tiefling_chthonic", "tiefling", ["chill_touch", "thaumaturgy"]),
])
def test_compatible_shipped_tier_one_choices_materialize_without_substitution(tmp_path, source, species, spells):
    package, declaration, setting = _plan_participant_authority()
    data = _participant("Preserved choice " + source)
    data.update(species=species, spellcasting_grants=[{"source": source, "ability": "intelligence", "spells": spells}])
    authored = _starting_state()
    authored["prepared_spells"] = spells
    profile = "Synthetic QA character exercising the unchanged shipped " + source + " policy."
    saved = save_reusable_participant(tmp_path, operation_id="preserved-choice", role="player_character",
        summary="Shipped choice compatibility", authored_profile=profile, participant=data,
        entity_state=authored, package=package, declaration=declaration, campaign_setting=setting)
    with AuthorityOwner.open(tmp_path / "choice.db", "qa") as owner:
        selection = resolve_campaign_library_selection(owner, tmp_path, candidate_id=package.package_id,
            setting_id="pneuma.severed-lands", plan_id="last-cart-through")
        participants, state, _ = materialize_library_party(owner, selection, operation_id="start-choice",
            player_character_id=saved.artifact_id, companion_ids=())
        assert list(participants[0].data["spellcasting_grants"][0]["spells"]) == spells
        assert participants[0].data["spellcasting_grants"][0]["source"] == source
        assert state

def test_chthonic_level_three_casting_grants_remain_complete_with_retained_false_life():
    _, data, _ = _spell_case("false_life")
    data.update(species="tiefling", level=3, classes=[{"id":"fighter", "level":3}], wizard_spellbook=[],
        spellcasting_grants=[{"source":"tiefling_chthonic", "ability":"intelligence",
            "spells":["chill_touch", "thaumaturgy", "false_life"]}])
    _validate(data, "", complete=True)
    assert _validate(data, "false_life")["_grant_free_pool"] == "false_life"


from scripts.srd_spell_grants import grant_policies

ADAPTED_SOURCES = ["elf_drow", "elf_high", "elf_wood", "gnome_forest", "gnome_rock",
                   "tiefling_chthonic", "tiefling_infernal"]

def _adapted_grants(source, level):
    policy = grant_policies()[source]
    spells = [name for name, acquired in policy["fixed"].items() if acquired <= level]
    if source == "elf_high":
        spells.insert(0, "fire_bolt")
    return policy, spells

@pytest.mark.parametrize("source", ADAPTED_SOURCES)
@pytest.mark.parametrize("level", [1, 3, 5])
def test_lite_ancestry_complete_acquisition_and_early_spell_rejection(source, level):
    policy, spells = _adapted_grants(source, level)
    _, data, _ = _spell_case("fire_bolt")
    data.update(level=level, classes=[{"id":"fighter", "level":level}], species=policy["species"],
        wizard_spellbook=[], spellcasting_grants=[{"source":source,"ability":"intelligence","spells":spells}])
    _validate(data, "", complete=True)
    for spell, acquired in policy["fixed"].items():
        if acquired > level:
            early = deepcopy(data)
            early["spellcasting_grants"][0]["spells"].append(spell)
            with pytest.raises(RulesetPreconditionRejected):
                _validate(early, "", complete=True)

@pytest.mark.parametrize("source", ADAPTED_SOURCES)
def test_all_adapted_tier_one_choices_materialize_normally(tmp_path, source):
    policy, spells = _adapted_grants(source, 1)
    test_compatible_shipped_tier_one_choices_materialize_without_substitution(
        tmp_path, source, policy["species"], spells)

@pytest.mark.parametrize("source,spell,original_pool", [
    ("elf_drow","bane","faerie_fire"), ("elf_drow","blur","darkness"),
    ("elf_high","longstrider","detect_magic"),
    ("elf_wood","enhance_ability","pass_without_trace"),
    ("tiefling_chthonic","blindness_deafness","ray_of_enfeeblement"),
    ("tiefling_infernal","burning_hands","hellish_rebuke"),
    ("tiefling_infernal","blindness_deafness","darkness"),
])
def test_adapted_leveled_spells_retain_original_charge_identity_and_limit(source, spell, original_pool):
    policy, spells = _adapted_grants(source, 5)
    _, data, _ = _spell_case("fire_bolt")
    data.update(level=5, classes=[{"id":"fighter","level":5}], species=policy["species"],
        wizard_spellbook=[], spellcasting_grants=[{"source":source,"ability":"intelligence","spells":spells}])
    context = _validate(data, spell, source)
    assert context["_grant_free_pool"] == original_pool
    assert context["_grant_free_limit"] == 1
    assert "Lite adaptation" in context["_grant_free_label"]

def test_infernal_adaptation_does_not_reset_spent_charge_and_persists(tmp_path):
    from tests.test_srd_lite_foundation import Case, VARIANT_ID
    from tests.lite_fixture_archive import archive_closed_case
    case = Case(tmp_path / "spent-charge.db", "burning_hands",
        participant_changes={"species":"tiefling","level":3,"classes":[{"id":"fighter","level":3}],
            "wizard_spellbook":[], "spellcasting_grants":[{"source":"tiefling_infernal","ability":"intelligence",
                "spells":["fire_bolt","thaumaturgy","burning_hands"]}]},
        entity_changes={"hero":{"spell_grant_uses":{"hellish_rebuke":1}}})
    try:
        parameters = case.parameters | {"slot_resource":"feature", "targets":["guard"], "all_targets_in_area":True}
        case.reject("cast_spell_outside_combat", parameters, match="feature use")
        saved = case.snapshot();case.reopen();assert case.snapshot() == saved
        assert case.entities()["hero"]["spell_grant_uses"] == {"hellish_rebuke":1}
        for kind, hours in [("short",1),("long",8)]:
            case.act("begin_srd_rest", target_state="hero", kind=kind, activity="light" if kind=="short" else "sleep")
            case.act("advance_srd_lasting_time", elapsed_rounds=hours*600)
            case.act("complete_srd_rest", target_state="hero")
            if kind=="short":case.reject("cast_spell_outside_combat", parameters, match="feature use")
        cast, command = case.act("cast_spell_outside_combat", **parameters)
        assert case.entities()["hero"]["spell_grant_uses"] == {"hellish_rebuke":1}
        saved=case.snapshot();assert case.execute(command)==cast
        case.reopen();assert case.execute(command)==cast and case.snapshot()==saved
        case.reject("cast_spell_outside_combat", parameters, match="feature use")
    finally:
        archive_closed_case(case)
