"""Level-up changes earn new spells while retaining source-bound replacement limits."""

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_advancement import PACKAGE_ID, award, build, command, create, resolve
from tests.test_srd_spell_result_cards import _spell_case

WIZARD_SPELLS = [
    "fire_bolt", "mage_hand", "light", "magic_missile", "burning_hands", "shield", "sleep",
]
WIZARD_BOOK = ["magic_missile", "burning_hands", "shield", "sleep", "chromatic_orb", "mage_armor"]


def _create(owner, source="wizard"):
    if source == "wizard":
        spells, ability, class_id = WIZARD_SPELLS, "intelligence", "wizard"
    else:
        spells = ["fire_bolt", "ray_of_frost", "magic_missile"]
        ability, class_id = "wisdom", "fighter"
    create(owner, participant_overrides={
        "classes": [{"id": class_id, "level": 1}],
        "origin_feats": ["savage_attacker"] if source == "wizard" else ["magic_initiate"],
        "spellcasting_grants": [{"source": source, "ability": ability, "spells": spells}],
        "wizard_spellbook": WIZARD_BOOK if source == "wizard" else [],
    }, entity_overrides={"prepared_spells": spells})
    award(owner)


def _proposal(owner, class_id):
    proposal = build(owner)
    proposal["class_id"] = class_id
    data = owner.snapshot("advancement").participants[0].data
    target = proposal["build"][0]
    target["spellcasting"] = thaw_json_value(data["spellcasting_grants"])
    target["wizard_spellbook"] = list(data.get("wizard_spellbook", []))
    target["prepared_spells"] = [s for g in target["spellcasting"] for s in g["spells"]]
    return proposal, target


@pytest.mark.parametrize("problem", [
    "cantrip", "repertoire", "book", "removed_source", "prepared", "foreign_spell",
    "missing_new_spell",
])
def test_invalid_spell_changes_are_rejected_before_level_confirmation(tmp_path, problem):
    with AuthorityOwner.open(tmp_path / "invalid.db", "test") as owner:
        _create(owner)
        proposal, target = _proposal(owner, "wizard")
        if problem == "cantrip":
            target["spellcasting"][0]["spells"][0] = "ray_of_frost"
        elif problem == "repertoire":
            target["spellcasting"][0]["spells"][3] = "chromatic_orb"
        elif problem == "book":
            target["wizard_spellbook"].remove("mage_armor")
        elif problem == "removed_source":
            target["spellcasting"] = []
        elif problem == "foreign_spell":
            target["spellcasting"][0]["spells"].append("cure_wounds")
        if problem != "prepared":
            target["prepared_spells"] = [s for g in target["spellcasting"] for s in g["spells"]]
        else:
            target["prepared_spells"] = []
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "invalid"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants


@pytest.mark.parametrize("problem", ["ability", "two_replacements"])
def test_feat_ability_and_single_replacement_are_preserved_on_any_class_level(tmp_path, problem):
    with AuthorityOwner.open(tmp_path / "feat.db", "test") as owner:
        _create(owner, "magic_initiate_wizard")
        proposal, target = _proposal(owner, "fighter")
        grant = target["spellcasting"][0]
        if problem == "ability":
            grant["ability"] = "intelligence"
        else:
            grant["spells"] = ["chill_touch", "ray_of_frost", "shield"]
        target["prepared_spells"] = grant["spells"]
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "invalid"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants


def test_new_caster_class_requires_its_complete_spell_choices_before_confirmation(tmp_path):
    with AuthorityOwner.open(tmp_path / "missing-class.db", "test") as owner:
        create(owner, multiclass_ready=True)
        award(owner)
        proposal = build(owner)
        proposal["class_id"] = "wizard"
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants


def test_subclass_choice_must_be_saved_as_data_before_confirmation(tmp_path):
    with AuthorityOwner.open(tmp_path / "subclass.db", "test") as owner:
        old_spells = WIZARD_SPELLS + ["mage_armor"]
        create(owner, level=2, participant_overrides={
            "classes": [{"id": "wizard", "level": 2}],
            "spellcasting_grants": [{"source": "wizard", "ability": "intelligence",
                                     "spells": old_spells}],
            "wizard_spellbook": WIZARD_BOOK + ["longstrider", "false_life"],
        }, entity_overrides={"prepared_spells": old_spells})
        award(owner)
        proposal, target = _proposal(owner, "wizard")
        target["wizard_spellbook"] += ["ray_of_sickness", "protection_from_evil_and_good"]
        target["spellcasting"][0]["spells"].append("ray_of_sickness")
        target["prepared_spells"] = target["spellcasting"][0]["spells"]
        target["features"] = [{"id": "subclass", "source": "SRD Wizard", "choice": "Evoker"}]
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants


@pytest.mark.parametrize("subclasses", [{"wizard": "evoker"}, {"cleric": "life_domain"}])
def test_subclass_requires_its_own_class_at_level_three(tmp_path, subclasses):
    with AuthorityOwner.open(tmp_path / "early-subclass.db", "test") as owner:
        _create(owner)
        proposal, target = _proposal(owner, "wizard")
        target["wizard_spellbook"] += ["ray_of_sickness", "protection_from_evil_and_good"]
        target["spellcasting"][0]["spells"].append("ray_of_sickness")
        target["prepared_spells"] = target["spellcasting"][0]["spells"]
        target["subclasses"] = subclasses
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="subclass"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants


def test_wizard_earns_book_and_prepared_growth_then_reopens_without_replacing_old_spells(tmp_path):
    database = tmp_path / "wizard.db"
    with AuthorityOwner.open(database, "test") as owner:
        _create(owner)
        proposal, target = _proposal(owner, "wizard")
        target["wizard_spellbook"] += ["ray_of_sickness", "protection_from_evil_and_good"]
        target["spellcasting"][0]["spells"].append("ray_of_sickness")
        target["prepared_spells"] = target["spellcasting"][0]["spells"]
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        receipt = resolve(owner, waiting)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("advancement")
        assert saved.participants[0].data["level"] == 2
        assert saved.participants[0].data["wizard_spellbook"] == target["wizard_spellbook"]
        assert saved.state[f"{PACKAGE_ID}.entities"]["hero"]["prepared_spells"] == target[
            "prepared_spells"
        ]
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("advancement").participants == saved.participants
        assert owner.snapshot("advancement").state == saved.state


def test_sorcerer_can_replace_one_cantrip_and_one_spell_while_learning_two_more(tmp_path):
    with AuthorityOwner.open(tmp_path / "sorcerer.db", "test") as owner:
        old = ["fire_bolt", "mage_hand", "light", "ray_of_frost", "magic_missile", "shield"]
        create(owner, participant_overrides={
            "classes": [{"id": "sorcerer", "level": 1}],
            "spellcasting_grants": [{"source": "sorcerer", "ability": "charisma", "spells": old}],
        }, entity_overrides={"prepared_spells": old})
        award(owner)
        proposal, target = _proposal(owner, "sorcerer")
        new = ["chill_touch", "mage_hand", "light", "ray_of_frost",
               "magic_missile", "burning_hands", "ice_knife", "sleep"]
        target["spellcasting"][0]["spells"] = target["prepared_spells"] = new
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        receipt = resolve(owner, waiting)
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("advancement").participants[0].data["spellcasting_grants"][0][
            "spells"
        ] == new


def test_magic_initiate_allows_one_same_level_replacement_on_a_fighter_level(tmp_path):
    with AuthorityOwner.open(tmp_path / "feat-ok.db", "test") as owner:
        _create(owner, "magic_initiate_wizard")
        proposal, target = _proposal(owner, "fighter")
        target["spellcasting"][0]["spells"][0] = "chill_touch"
        target["prepared_spells"] = target["spellcasting"][0]["spells"]
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert resolve(owner, waiting).status is ActionStatus.COMMITTED


def test_arcanum_replacement_limit_is_shared_across_spell_levels():
    state, prior, _ = _spell_case("power_word_kill")
    prior.update(level=17, classes=[{"id": "warlock", "level": 17}], wizard_spellbook=[])
    prior["spellcasting_grants"] = [
        {"source": "mystic_arcanum_7", "ability": "charisma", "spells": ["finger_of_death"]},
        {"source": "mystic_arcanum_8", "ability": "charisma", "spells": ["befuddlement"]},
    ]
    grants = [
        {"source": "mystic_arcanum_7", "ability": "charisma", "spells": ["etherealness"]},
        {"source": "mystic_arcanum_8", "ability": "charisma", "spells": ["power_word_stun"]},
    ]
    with pytest.raises(RulesetPreconditionRejected, match="Shared class"):
        _run("validate_srd_level_spell_changes", state, participant_data=prior, caster="hero",
             grants=grants,
             prepared=["etherealness", "power_word_stun"], spellbook=[],
             gained_class="warlock", class_level=18)
    grants[1] = prior["spellcasting_grants"][1]
    result = _run("validate_srd_level_spell_changes", state, participant_data=prior, caster="hero",
                  grants=grants,
                  prepared=["etherealness", "befuddlement"], spellbook=[],
                  gained_class="warlock", class_level=18)
    assert result.result["status"] == "committed"


@pytest.mark.parametrize("old_level", [2, 4])
@pytest.mark.parametrize("choice", ["complete", "wrong_school", "skip_bonus"])
def test_evocation_savant_additions_use_only_earned_evocation_book_entries(
    tmp_path, old_level, choice,
):
    old_book = WIZARD_BOOK + ["longstrider", "false_life"]
    additions = ["darkvision", "blur", "scorching_ray", "shatter"]
    if old_level == 4:
        old_book += additions + ["hold_person", "web"]
        additions = ["fly", "dispel_magic", "fireball"]
    if choice == "wrong_school":
        additions[-1] = "web" if old_level == 2 else "sleet_storm"
    elif choice == "skip_bonus":
        additions = additions[:2]
    old_spells = WIZARD_SPELLS + ["mage_armor"]
    if old_level == 4:
        old_spells += ["ray_of_frost", "darkvision", "blur"]
    with AuthorityOwner.open(tmp_path / "evoker.db", "test") as owner:
        create(owner, level=old_level, participant_overrides={
            "classes": [{"id": "wizard", "level": old_level}],
            "subclasses": {"wizard": "evoker"} if old_level >= 3 else {},
            "spellcasting_grants": [{"source": "wizard", "ability": "intelligence",
                                     "spells": old_spells}],
            "wizard_spellbook": old_book,
        }, entity_overrides={"prepared_spells": old_spells})
        award(owner)
        proposal, target = _proposal(owner, "wizard")
        target["subclasses"] = {"wizard": "evoker"}
        target["features"] = [{"id": "subclass", "source": "SRD Evoker", "choice": "Evoker"}]
        target["wizard_spellbook"] += additions
        target["spellcasting"][0]["spells"] += (
            ["darkvision"] if old_level == 2 else ["fly", "dispel_magic"]
        )
        target["prepared_spells"] = target["spellcasting"][0]["spells"]
        before = owner.snapshot("advancement")
        if choice == "wrong_school" or (choice == "skip_bonus" and old_level == 2):
            with pytest.raises(CommandRejected):
                owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
            assert owner.snapshot("advancement").state == before.state
            assert owner.snapshot("advancement").participants == before.participants
        else:
            waiting = owner.execute_action(
                command(owner, "level_up_srd_character", proposal, "level"),
            )
            assert resolve(owner, waiting).status is ActionStatus.COMMITTED
            assert owner.snapshot("advancement").participants[0].data["wizard_spellbook"] == (
                old_book + additions
            )


def test_prior_copied_book_entries_do_not_replace_new_wizard_level_choices(tmp_path):
    with AuthorityOwner.open(tmp_path / "copied-book.db", "test") as owner:
        create(owner, participant_overrides={
            "classes": [{"id": "wizard", "level": 1}],
            "spellcasting_grants": [{"source": "wizard", "ability": "intelligence",
                                     "spells": WIZARD_SPELLS}],
            "wizard_spellbook": WIZARD_BOOK + ["longstrider", "false_life"],
        }, entity_overrides={"prepared_spells": WIZARD_SPELLS})
        award(owner)
        proposal, target = _proposal(owner, "wizard")
        target["spellcasting"][0]["spells"].append("longstrider")
        target["prepared_spells"] = target["spellcasting"][0]["spells"]
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="spellbook"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert owner.snapshot("advancement").state == before.state
        assert owner.snapshot("advancement").participants == before.participants
