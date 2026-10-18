"""Rest-earned preparation changes preserve sources, spellbooks and saved use limits."""

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_public_spell_casting import SRD, _create_spell_campaign
from tests.test_srd_spell_result_cards import _spell_case


def _rest(owner, kind):
    _act(owner, "begin_srd_rest", target_state="hero", kind=kind,
         activity="sleep" if kind == "long" else "light")
    _act(owner, "advance_srd_lasting_time", elapsed_rounds=4800 if kind == "long" else 600)
    _act(owner, "complete_srd_rest", target_state="hero")


def _prepare(owner, source, spells):
    return _act(owner, "prepare_srd_spells", caster="hero", caster_state="hero",
                source=source, spells=spells, spellbook_available=True)


def test_wizard_rest_preparation_is_atomic_saved_and_preserves_other_grants_and_uses(tmp_path):
    database = tmp_path / "prepare.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_spell_campaign(owner, "magic_missile", participant_overrides={
            "level": 3, "classes": [{"id": "wizard", "level": 3}],
            "wizard_spellbook": ["magic_missile", "ray_of_sickness"],
            "spellcasting_grants": [
                {"source": "wizard", "ability": "intelligence",
                 "spells": ["magic_missile", "fire_bolt"]},
                {"source": "magic_initiate_cleric", "ability": "wisdom",
                 "spells": ["cure_wounds"]},
            ],
        }, entity_overrides={
            "prepared_spells": ["magic_missile", "fire_bolt", "cure_wounds"],
            "spell_grant_uses": {"magic_initiate_cleric": 1},
        })
        names = {t.name for t in StatelessMcpMechanics(owner).list_tools("casting")}
        assert "prepare_srd_spells" in names
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="rest"):
            _prepare(owner, "wizard", ["ray_of_sickness", "ray_of_frost"])
        assert owner.snapshot("casting").state == before.state
        _rest(owner, "long")
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="spellbook"):
            _prepare(owner, "wizard", ["acid_arrow", "ray_of_frost"])
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").participants == before.participants
        receipt, command = _prepare(owner, "wizard", ["ray_of_sickness", "ray_of_frost"])
        assert receipt.status is ActionStatus.COMMITTED
        assert receipt.result["result_card"]["title"] == "Spells prepared"
        details = {row["label"]: row["value"] for row in receipt.result["result_card"]["details"]}
        assert details == {"Added": ["Ray of Sickness", "Ray of Frost"],
                           "Removed": ["Magic Missile", "Fire Bolt"]}
        saved = owner.snapshot("casting")
        assert saved.participants[0].data["spellcasting_grants"] == [
            {"source": "wizard", "ability": "intelligence",
             "spells": ["ray_of_sickness", "ray_of_frost"]},
            {"source": "magic_initiate_cleric", "ability": "wisdom", "spells": ["cure_wounds"]},
        ]
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert set(hero["prepared_spells"]) == {"ray_of_sickness", "ray_of_frost", "cure_wounds"}
        assert hero["resources"] == before.state[f"{SRD}.entities"]["hero"]["resources"]
        assert (
            hero["spell_grant_uses"] == before.state[f"{SRD}.entities"]["hero"]["spell_grant_uses"]
        )
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").participants == saved.participants
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.snapshot("casting").participants == saved.participants
        with pytest.raises(MechanicsRejected, match="replacement"):
            _prepare(owner, "wizard", ["magic_missile", "chill_touch"])
        _, _, cast = _spell_case("ray_of_sickness")
        _act(owner, "cast_spell_outside_combat", **cast)
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="rest"):
            _prepare(owner, "wizard", ["magic_missile", "ray_of_frost"])
        assert owner.snapshot("casting").state == before.state


def test_paladin_can_replace_only_one_spell_per_long_rest_across_calls(tmp_path):
    with AuthorityOwner.open(tmp_path / "paladin.db", "casting-owner") as owner:
        _create_spell_campaign(owner, "cure_wounds", participant_overrides={
            "level": 3, "classes": [{"id": "paladin", "level": 3}],
            "spellcasting_grants": [{"source": "paladin", "ability": "charisma",
                                      "spells": ["bless", "cure_wounds"]}],
        }, entity_overrides={"prepared_spells": ["bless", "cure_wounds"]})
        _rest(owner, "long")
        _prepare(owner, "paladin", ["shield_of_faith", "cure_wounds"])
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="replacement"):
            _prepare(owner, "paladin", ["shield_of_faith", "divine_smite"])
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").participants == before.participants


@pytest.mark.parametrize("level", [4, 5])
def test_memorize_spell_requires_wizard_five_and_one_short_rest_replacement(tmp_path, level):
    with AuthorityOwner.open(tmp_path / f"wizard-{level}.db", "casting-owner") as owner:
        _create_spell_campaign(owner, "magic_missile", participant_overrides={
            "level": level, "classes": [{"id": "wizard", "level": level}],
            "wizard_spellbook": ["magic_missile", "ray_of_sickness"],
        })
        _rest(owner, "short")
        before = owner.snapshot("casting")
        if level == 4:
            with pytest.raises(MechanicsRejected, match="replacement"):
                _prepare(owner, "wizard", ["ray_of_sickness"])
            assert owner.snapshot("casting").state == before.state
        else:
            _prepare(owner, "wizard", ["ray_of_sickness"])
            before = owner.snapshot("casting")
            with pytest.raises(MechanicsRejected, match="replacement"):
                _prepare(owner, "wizard", ["magic_missile"])
            assert owner.snapshot("casting").state == before.state
            _rest(owner, "short")
            _prepare(owner, "wizard", ["magic_missile"])
            _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
            with pytest.raises(MechanicsRejected, match="rest"):
                _prepare(owner, "wizard", ["ray_of_sickness"])


@pytest.mark.parametrize("source,old_spells,new_spells,profile", [
    ("cleric", ["bless", "cure_wounds"], ["shield", "cure_wounds"], {
        "level": 3, "classes": [{"id": "cleric", "level": 3}],
    }),
    ("elf_high", ["fire_bolt", "detect_magic"], ["ray_of_frost", "detect_magic"], {
        "species": "elf", "level": 3, "classes": [{"id": "fighter", "level": 3}],
    }),
])
def test_rest_changes_keep_source_spell_lists_and_fixed_lineage_spells(
    tmp_path, source, old_spells, new_spells, profile,
):
    if source == "elf_high":
        from sylvia_gm.domain.errors import CommandRejected
        with pytest.raises(CommandRejected, match="outside its enum"):
            with AuthorityOwner.open(tmp_path / "source.db", "casting-owner") as owner:
                _create_spell_campaign(owner, "cure_wounds", participant_overrides=profile | {"spellcasting_grants": [{"source": source, "ability": "wisdom", "spells": old_spells}]}, entity_overrides={"prepared_spells": old_spells})
        return
    with AuthorityOwner.open(tmp_path / "source.db", "casting-owner") as owner:
        _create_spell_campaign(owner, "cure_wounds", participant_overrides=profile | {
            "spellcasting_grants": [{"source": source, "ability": "wisdom", "spells": old_spells}],
        }, entity_overrides={"prepared_spells": old_spells})
        _rest(owner, "long")
        before = owner.snapshot("casting")
        if source == "cleric":
            # Shield is a Wizard/Sorcerer spell: a Long Rest cannot grant a different class list.
            with pytest.raises(MechanicsRejected, match="actual class"):
                _prepare(owner, source, new_spells)
            assert owner.snapshot("casting").participants == before.participants
            assert owner.snapshot("casting").state == before.state
            new_spells = ["shield_of_faith", "guiding_bolt"]
        _prepare(owner, source, new_spells)
        assert owner.snapshot("casting").participants[0].data["spellcasting_grants"][0][
            "spells"
        ] == new_spells


def test_interrupted_long_rest_retains_earned_short_rest_preparation():
    state, data, _ = _spell_case("magic_missile")
    data.update(level=5, classes=[{"id": "wizard", "level": 5}],
                wizard_spellbook=["magic_missile", "ray_of_sickness"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("begin_srd_rest", state, target_state="hero", kind="long", activity="light")
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _run("interrupt_srd_rest", state, target_state="hero", reason="damage")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["rest_phase"] == "interrupted"
    assert hero["spell_preparation_kind"] == "short"
    assert hero["spell_preparation_open"] is True
    _run("prepare_srd_spells", state, participant_data=data, caster="hero", caster_state="hero",
         source="wizard", spells=["ray_of_sickness"], spellbook_available=True)
    assert state[f"{SRD}.entities"]["hero"]["prepared_spells"] == ["ray_of_sickness"]
