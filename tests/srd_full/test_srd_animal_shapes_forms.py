"""One Animal Shapes Magic action selects actual different forms without refreshing TempHP."""

from copy import deepcopy
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_transformation_spells import _followup_tools

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_information_self_casting as casting
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_wave6_transformations as forms
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act


@pytest.fixture(autouse=True)
def _authored(monkeypatch):
    runtime, _ = casting._runtime()
    runtime = deepcopy(runtime)
    changed = _followup_tools()
    _normalize(changed)
    original = runtime["tools"]["change_srd_transformation"]
    effects = original["effects"]
    start = next(i for i, e in enumerate(effects) if e.get("call") == "project_srd_transformation")
    finish = (
        max(
            i
            for i, e in enumerate(effects)
            if e.get("for_each", {}).get("collection") == "_change_source_ids"
        )
        + 1
    )
    original["effects"] = (
        effects[:start] + changed["change_srd_transformation"]["effects"] + effects[finish:]
    )
    original["parameters"] = changed["change_srd_transformation"]["parameters"]
    content = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()
    monkeypatch.setattr(casting, "_runtime", lambda: (runtime, content))
    casting._mechanic.cache_clear()
    casting._authored.__wrapped__(monkeypatch)
    yield
    casting._mechanic.cache_clear()


def test_existing_single_form_change_shortcut_keeps_first_temporary_hp():
    forms.test_animal_shapes_retains_mental_scores_and_first_hp_on_later_magic_action()


def test_paid_two_recipient_changes_use_one_magic_action_and_survive_reopen_retry(
    tmp_path, monkeypatch
):
    from sylvia_gm.application import bundled_packages
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_dream_spell import _linked_drafts

    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", casting._runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", set())
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", forms._setup)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / "animal-forms.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "animal_shapes")
        parameters.update(
            targets=["guard", "other"], all_targets_willing=True, transformation_form="brown_bear"
        )
        paid, cast_command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert paid.status is ActionStatus.COMMITTED
        assert StatelessMcpMechanics(owner).execute_action(cast_command) == paid
        sources = owner.snapshot("casting").state[f"{SRD}.state"]["transformation_sources"]
        source_ids = {source["target_state_id"]: source_id for source_id, source in sources.items()}
        lifetimes = {
            source_id: (source["serial"], source["started_tick"], source["expires_tick"])
            for source_id, source in sources.items()
        }
        _act(owner, "start_group_combat_encounter", combatants=["hero", "guard", "other"])
        for _ in range(3):
            _act(owner, "begin_combat_turn")
            if owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "hero":
                break
            _act(owner, "end_combat_turn")
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["active_actor"] == "hero"
        choices = [
            {"source_id": source_ids["guard"], "form_id": "camel"},
            {"source_id": source_ids["other"], "form_id": "giant_rat"},
        ]
        mismatch = deepcopy(choices)
        mismatch[1]["target_character"] = "hero"
        with pytest.raises(MechanicsRejected, match="match"):
            _act(owner, "change_srd_transformation", actor_state="hero", source_forms=mismatch)
        assert owner.snapshot("casting").state == before.state
        changed, change_command = _act(
            owner, "change_srd_transformation", actor_state="hero", source_forms=choices
        )
        assert changed.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        for target, speed, temp_hp in (("guard", 50, 22), ("other", 30, 22)):
            subject = saved.state[f"{SRD}.entities"][target]
            assert subject["speed"] == speed and subject["temp_hp"] == temp_hp
            assert subject["form_current_source_id"] == source_ids[target]
            assert subject["form_ability_scores"]["intelligence"] == 12
        sources = saved.state[f"{SRD}.state"]["transformation_sources"]
        assert sources[source_ids["guard"]]["form_id"] == "camel"
        assert sources[source_ids["other"]]["form_id"] == "giant_rat"
        assert {
            source_id: (source["serial"], source["started_tick"], source["expires_tick"])
            for source_id, source in sources.items()
        } == lifetimes
        with pytest.raises(MechanicsRejected, match="action"):
            _act(owner, "change_srd_transformation", actor_state="hero", source_forms=choices)
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(reopened).execute_action(change_command) == changed
        assert reopened.snapshot("casting").state == saved.state
