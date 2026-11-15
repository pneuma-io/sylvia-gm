"""Paid source Mummy creation and real later mixed actions survive installed-package saves."""

import io
import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_native_mummy_creation import PUBLIC_TOOLS, extend_runtime
from scripts.srd_full.srd_parameter_declarations import compact_runtime, restore_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import (
    ActionStatus,
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
)
from tests.srd_full.test_srd_native_mummy_routine import (
    _ogre,
)
from tests.srd_full.test_srd_native_mummy_routine import (
    mummy_runtime as mummy_runtime,
)
from tests.srd_full.test_srd_native_wight_rise import rise_owner
from tests.srd_full.test_srd_spell_summoning import SRD, _created_undead_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mummy_creation_runtime(mummy_runtime):
    from scripts.srd_full.srd_reaction_spells import _attack_window, _complete_window
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = extend_runtime(restore_runtime(mummy_runtime[1].decode()))
    assert extend_runtime(text) == text
    assert sha256((ROOT / "scripts/srd_reaction_spells.py").read_bytes()).hexdigest() == (
        "70f1f2a9b391366d488301e46caeeb1c988bf8c1799550e134ec54b6756b5a3b"
    )
    tools = _load_runtime(text)["tools"]
    saved = _attack_window(
        tools["resolve_adjudicated_creature_attack"],
        "begin_srd_creature_attack",
        "actor",
        "creature_attack",
    )
    tools.update(saved)
    saved["complete_srd_pending_action"] = _complete_window(tools)
    text = _replace_tools(text, saved)
    runtime = _load_runtime(text)
    text = compact_runtime(text)
    assert _load_runtime(restore_runtime(text)) == runtime
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="native-mummy-creation")
    return runtime, text.encode()


@pytest.fixture(scope="module")
def mummy_archive(mummy_creation_runtime):
    from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes

    runtime, encoded = mummy_creation_runtime
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        name = declaration["name"]
        declaration["input_schema"] = _tool_schema(runtime["tools"][name], tool_name=name)
    assert not (PUBLIC_TOOLS & {t["name"] for t in record["tools"]})
    for name in sorted(PUBLIC_TOOLS):
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "input_schema": _tool_schema(runtime["tools"][name], tool_name=name),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
        )
    record["tools"].sort(key=lambda declaration: declaration["name"])
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "source-mummy-owning-fixture",
            "kind": "ruleset_runtime",
            "family": "source-mummy-owning-fixture",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": sorted(PUBLIC_TOOLS),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["source-mummy-paid-creation-mixed-native-actions"]},
        }
    )
    content["inclusions"].sort(key=lambda inclusion: inclusion["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def _campaign(owner, archive):
    """Keep the selected actual corpse and printed Ogre statistics through campaign admission."""
    package = owner.install_package_archive(io.BytesIO(archive), install_id="casting")
    exact = next(entry for entry in package.package_lock.entries if entry.package_id == SRD)
    record = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/package-record.json").read_text())
    artifact = next(a for a in record["participant_artifacts"] if a["kind"] == "player_character")
    state, data, parameters = _created_undead_case(1, 9)
    state[f"{SRD}.combat"].update(
        encounter_active=False,
        turn_active=False,
        active_actor=None,
        phase="exploration",
        action_available=False,
    )
    parameters["created_undead_form"] = "mummy"
    parameters["created_undead_bodies"][0].update(
        individual_id="mummy_paid", other_languages=["elvish", "infernal"]
    )
    _ogre(state, "ogre_a", [15, 0, 0])
    _ogre(state, "ogre_b", [5, 12.5, 0])
    data.update(
        species="human",
        background="sage",
        size_rank=1,
        saving_throw_proficiencies=["intelligence", "wisdom"],
        origin_feats=["magic_initiate"],
    )
    state[f"{SRD}.entities"]["hero"].update(
        creature_type="humanoid", initiative_modifier=(data["abilities"]["dexterity"] - 10) // 2
    )
    state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] = 1
    owner.create_campaign(
        CampaignCreate(
            campaign_id="casting",
            display_name="Source Mummy",
            writer=WriterRole.DM,
            package_lock=package.package_lock,
            turn_id="casting-turn",
            participants=(
                Participant(
                    participant_id="hero",
                    kind="player_character",
                    package_id=SRD,
                    package_version=exact.version,
                    package_sha256=exact.sha256,
                    schema_id=artifact["schema_id"],
                    schema_sha256=artifact["schema_sha256"],
                    validation_status=ParticipantValidationStatus.VALIDATED,
                    data=data,
                ),
            ),
            initial_state=tuple(
                StateValue(SRD, key.removeprefix(f"{SRD}."), value) for key, value in state.items()
            ),
        )
    )
    return parameters


def test_public_paid_mummy_creation_mixed_actions_saved_fist_and_native_curse(
    tmp_path,
    monkeypatch,
    mummy_archive,
    mummy_creation_runtime,
):
    from tests.srd_full.test_srd_action_budget import _run
    from tests.srd_full.test_srd_extended_casting import _begin

    rise_owner.__wrapped__(monkeypatch, mummy_creation_runtime)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "source-mummy.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        parameters = _campaign(owner, mummy_archive)
        mechanics = StatelessMcpMechanics(owner)
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="mummy-night",
            idempotency_key="mummy-night",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_map_time_of_day",
            parameters={"plane": "material", "phase": "night"},
        )

        def act(identity, name, **choices):
            command = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == receipt
            return command, receipt

        materials = deepcopy(
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["spell_materials"]
        )
        act("mummy-night", "record_srd_map_time_of_day", plane="material", phase="night")
        act("mummy-begin", "begin_srd_spell_cast", **_begin(parameters))
        act(
            "mummy-minute",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("mummy-fresh-night", "record_srd_map_time_of_day", plane="material", phase="night")
        created, creation = act("mummy-paid", "cast_spell_outside_combat", **parameters)
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["mummy_paid"]
        assert (body["hp"], body["max_hp"], body["armor_class"], body["speed"]) == (58, 58, 11, 20)
        assert body["languages"] == ["common", "elvish", "infernal"]
        assert body["created_form_id"] == "mummy"
        assert saved.state[f"{SRD}.entities"]["guard"]["body_animated_actor_id"] == "mummy_paid"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 0
        assert saved.state[f"{SRD}.entities"]["hero"]["spell_materials"] == materials
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(created) == creation
        dice._rng.seed(5)
        act(
            "mummy-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "mummy_paid", "ogre_a", "ogre_b"],
        )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"] == "hero"
        act("mummy-caster-turn", "begin_combat_turn")
        act(
            "mummy-command",
            "command_srd_animated_undead",
            caster_state="hero",
            actor_ids=["mummy_paid"],
            order_kind="specific",
            action="attack",
        )
        act("mummy-caster-end", "end_combat_turn")
        act("mummy-caster-next", "advance_group_combat_encounter_turn")
        for index in range(3):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"mummy-turn-{index}", "begin_combat_turn")
            if actor == "mummy_paid":
                break
            act(f"mummy-end-{index}", "end_combat_turn")
            act(f"mummy-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The actual source Mummy must reach its own controlled initiative turn.")
        dice._rng.seed(31)
        _, glare = act(
            "mummy-native-glare",
            "use_srd_native_mummy_glare",
            actor_state="mummy_paid",
            target_state="ogre_a",
            use_multiattack=True,
        )
        assert glare.result["context"]["_mummy_glare_outcome"] == "failure"
        assert glare.result["result_card"]["title"] == "Dreadful Glare"
        assert [(row["label"], row["value"]) for row in glare.result["result_card"]["summary"]] == [
            ("Save", "failure")
        ]
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["attacks_remaining"] == 2
        relation = _run(
            "qualify_srd_created_actor_attack_geometry",
            deepcopy(owner.snapshot("casting").state),
            actor_state="mummy_paid",
            target_state="ogre_a",
        ).result["context"]
        dice._rng.seed(5)
        begin, begun = act(
            "mummy-fist-begin",
            "begin_srd_creature_attack",
            target="ogre_a",
            attack_name="Rotting Fist",
            attack_modifier=5,
            attack_mode="melee",
            damage_expression="d10",
            damage_modifier=3,
            damage_type="bludgeoning",
            normal_range=5,
            long_range=5,
            attack_distance=relation["_created_attack_distance"],
            use_multiattack=True,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
            attack_uses_weapon=False,
        )
        pending = begun.result["context"]["_pending_action_id"]
        saved_roll = deepcopy(
            owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_actions"][pending][
                "attack_roll"
            ]
        )
        hp = owner.snapshot("casting").state[f"{SRD}.entities"]["ogre_a"]["hp"]
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(begin) == begun
        completed, completion = act(
            "mummy-fist-complete", "complete_srd_pending_action", pending_action_id=pending
        )
        context = completion.result["context"]
        assert context["_attack_roll"] == saved_roll
        assert context["_attack_outcome"] == "critical_hit"
        assert len(context["_mummy_fist_necrotic_roll"]["results"]) == 6
        assert context["_mummy_fist_necrotic_amount"] == sum(
            context["_mummy_fist_necrotic_roll"]["results"]
        )
        victim = owner.snapshot("casting").state[f"{SRD}.entities"]["ogre_a"]
        assert 0 < victim["hp"] < hp and victim["hit_point_recovery_blocked"] is True
        source = owner.snapshot("casting").state[f"{SRD}.state"]["timed_condition_sources"][
            victim["mummy_rot_source_ids"][0]
        ]
        assert context["_mummy_rot_source_id"] == victim["mummy_rot_source_ids"][0]
        assert context["_mummy_fist_dust"] is False
        assert hp - victim["hp"] == (
            context["_component_1_effective_damage"] + context["_component_2_effective_damage"]
        )
        assert context["_component_2_effective_damage"] == context["_mummy_fist_necrotic_amount"]
        rows = {row["label"]: row["value"] for row in completion.result["result_card"]["details"]}
        assert (
            "incoming " + str(context["_mummy_fist_necrotic_amount"]) in rows["Mummy Fist Necrotic"]
        )
        assert (
            str(context["_component_1_effective_damage"]) + " Bludgeoning"
            in (rows["Mummy Fist components"])
        )
        assert (
            str(context["_component_2_effective_damage"]) + " Necrotic"
            in rows["Mummy Fist components"]
        )
        assert rows["Mummy Rot"] == "Applied Mummy Rot."
        assert "Fist Dust" not in rows
        assert source["native_body_source"] == "mummy_paid" and source["save_dc"] is None
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["attacks_remaining"] == 1
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(completed) == completion
        assert owner.snapshot("casting").state == saved.state
