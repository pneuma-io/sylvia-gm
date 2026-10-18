"""Real archive admission, paid minute/night creation and saved native Wight Life Drain."""

import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from tests.srd_full.test_srd_native_wight_attacks import (
    _native_mummy_fist_runtime as _native_mummy_fist_runtime,
)
from tests.srd_full.test_srd_native_wight_attacks import (
    _native_mummy_glare_runtime as _native_mummy_glare_runtime,
)
from tests.srd_full.test_srd_native_wight_attacks import (
    _native_wight_runtime as _native_wight_runtime,
)
from tests.srd_full.test_srd_native_wight_attacks import (
    _native_wight_weapons_runtime as _native_wight_weapons_runtime,
)
from tests.srd_full.test_srd_spell_summoning import SRD, _admit_zombie_slam_target, _created_undead_case


@pytest.fixture(scope="module")
def _wight_public_runtime(_native_wight_weapons_runtime):
    from scripts.srd_full.srd_native_wight_creation import extend_runtime
    from scripts.srd_full.srd_parameter_declarations import compact_runtime
    from scripts.srd_full.srd_reaction_spells import _attack_window, _complete_window
    from scripts.srd_full.srd_spell_trigger_composition import _effect_lists
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = extend_runtime(_native_wight_weapons_runtime[1].decode())
    assert extend_runtime(text) == text
    tools = _load_runtime(text)["tools"]
    saved_attack = _attack_window(
        tools["resolve_adjudicated_creature_attack"],
        "begin_srd_creature_attack",
        "actor",
        "creature_attack",
    )
    tools.update(saved_attack)
    saved_attack["complete_srd_pending_action"] = _complete_window(tools)
    text = _replace_tools(text, saved_attack)
    runtime = _load_runtime(text)
    dependency = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-wight-public-creation/ruleset.yaml")
    ).read_bytes()
    assert sha256(dependency).hexdigest() == (
        "20c50891a72da87bb2f0156b73512e6c2de227b264aa3567f448c0d080713756"
    )
    actual = _load_runtime(dependency.decode())["tools"]
    borrowed = {}
    while True:
        missing = {
            effect["call"]
            for tool in runtime["tools"].values()
            for branch in _effect_lists(tool["effects"])
            for effect in branch
            if "call" in effect
        } - runtime["tools"].keys()
        if not missing:
            break
        assert missing <= actual.keys()
        borrowed.update({name: deepcopy(actual[name]) for name in missing})
        runtime["tools"].update({name: deepcopy(actual[name]) for name in missing})
    text = _replace_tools(text, borrowed) if borrowed else text
    original = _load_runtime(text)
    text = compact_runtime(text)
    assert compact_runtime(text) == text
    assert not validate_ruleset(text)
    encoded_runtime = _load_runtime(text)
    validate_content_shape("package_runtime", encoded_runtime, file="native-wight-public-cast")
    runtime = resolve_parameter_declarations(encoded_runtime)
    assert runtime == original
    return runtime, text.encode()


@pytest.fixture(scope="module")
def _wight_archive(_wight_public_runtime):
    from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
    from scripts.srd_full.srd_native_wight_creation import PUBLIC_TOOLS

    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes

    runtime, encoded = _wight_public_runtime
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
            "id": "source-wight-owning-fixture",
            "kind": "ruleset_runtime",
            "family": "source-wight-owning-fixture",
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
            "evidence": {"tests": ["source-wight-paid-creation-public-life-drain"]},
        }
    )
    content["inclusions"].sort(key=lambda inclusion: inclusion["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def test_public_paid_wight_minute_night_cost_native_turn_drain_reopen_and_retry(
    tmp_path,
    monkeypatch,
    _wight_archive,
    _wight_public_runtime,
):
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    def case(_spell):
        state, data, parameters = _created_undead_case(1, 8)
        parameters["created_undead_form"] = "wight"
        body = parameters["created_undead_bodies"][0]
        body.update(individual_id="wight_paid", other_languages=["infernal"])
        _admit_zombie_slam_target(state)
        state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
        state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
            {
                "id": "court-light",
                "map_id": "information",
                "shape": {"kind": "box", "min": [-100, -100, -100], "max": [100, 100, 100]},
                "metadata": {"plane": "material", "natural_sunlight": False},
            }
        )
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", case)
    monkeypatch.setattr(public, "bundled_srd_archive", lambda: _wight_archive)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "source-wight.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner, "create_undead", target_overrides={"hp": 0, "max_hp": 20}
        )
        mechanics = StatelessMcpMechanics(owner)
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="wight-night",
            idempotency_key="wight-night",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_map_time_of_day",
            parameters={"plane": "material", "phase": "night"},
        )

        def act(identity, name, **choices):
            action = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                tool_name=name,
                expected_revision=owner.snapshot("casting").revision,
                parameters=choices,
            )
            receipt = mechanics.execute_action(action)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == receipt
            return action, receipt

        materials = deepcopy(
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["spell_materials"]
        )
        act("wight-night", "record_srd_map_time_of_day", plane="material", phase="night")
        act("wight-begin", "begin_srd_spell_cast", **_begin(parameters))
        act(
            "wight-minute",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        act("wight-fresh-night", "record_srd_map_time_of_day", plane="material", phase="night")
        created, receipt = act("wight-paid", "cast_spell_outside_combat", **parameters)
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["wight_paid"]
        assert body["hp"] == body["max_hp"] == 82 and body["armor_class"] == 14
        assert body["languages"] == ["common", "infernal"]
        assert body["created_form_id"] == "wight"
        assert saved.state[f"{SRD}.entities"]["guard"]["body_animated_actor_id"] == "wight_paid"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        assert saved.state[f"{SRD}.entities"]["hero"]["spell_materials"] == materials
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(created) == receipt
        act(
            "wight-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "wight_paid", "other"],
        )
        for index in range(3):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"wight-turn-{index}", "begin_combat_turn")
            if actor == "hero":
                act(
                    "wight-real-command",
                    "command_srd_animated_undead",
                    caster_state="hero",
                    actor_ids=["wight_paid"],
                    order_kind="specific",
                    action="attack",
                )
            if actor == "wight_paid":
                break
            act(f"wight-end-{index}", "end_combat_turn")
            act(f"wight-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("Actual paid Wight did not reach its source-controlled independent turn.")
        before = deepcopy(owner.snapshot("casting").state[f"{SRD}.entities"]["hero"])
        dice._rng.seed(165)
        drain, drain_receipt = act(
            "wight-native-drain",
            "use_srd_native_wight_life_drain",
            actor_state="wight_paid",
            target_state="hero",
        )
        saved = owner.snapshot("casting")
        target = saved.state[f"{SRD}.entities"]["hero"]
        assert target["hp"] == before["hp"] - 3
        assert target["max_hp"] == before["max_hp"] - 3
        assert drain_receipt.result["result_card"]["title"] == "Life Drain"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        assert saved.state[f"{SRD}.entities"]["hero"]["spell_materials"] == materials
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(drain) == drain_receipt
        assert owner.snapshot("casting").state == saved.state
        act("wight-drain-end", "end_combat_turn")
        act("wight-drain-next", "advance_group_combat_encounter_turn")
        for index in range(3):
            actor = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"wight-weapon-turn-{index}", "begin_combat_turn")
            if actor == "hero":
                act(
                    "wight-weapon-command",
                    "command_srd_animated_undead",
                    caster_state="hero",
                    actor_ids=["wight_paid"],
                    order_kind="specific",
                    action="attack",
                )
            if actor == "wight_paid":
                break
            act(f"wight-weapon-end-{index}", "end_combat_turn")
            act(f"wight-weapon-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The Wight must reach its actual next independent turn.")
        rolls = []

        class WightDice:
            def randint(self, low, high):
                assert low == 1 and high in (8, 20)
                rolls.append(high)
                return 20 if high == 20 else (3 if rolls.count(8) <= 2 else 4)

        monkeypatch.setattr(dice, "_rng", WightDice())
        before = deepcopy(owner.snapshot("casting").state[f"{SRD}.entities"]["hero"])
        from tests.srd_full.test_srd_action_budget import _run
        from tests.srd_full.test_srd_native_wight_attacks import native_wight_weapons_owner

        native_wight_weapons_owner.__wrapped__(monkeypatch, _wight_public_runtime)
        relation = _run(
            "qualify_srd_created_actor_attack_geometry",
            deepcopy(owner.snapshot("casting").state),
            actor_state="wight_paid",
            target_state="hero",
        ).result["context"]
        begin, begun = act(
            "wight-sword-begin",
            "begin_srd_creature_attack",
            target="hero",
            attack_name="Necrotic Sword",
            attack_modifier=4,
            attack_mode="melee",
            damage_expression="d8",
            damage_modifier=2,
            damage_type="slashing",
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
            attack_uses_weapon=True,
        )
        pending = begun.result["context"]["_pending_action_id"]
        saved_roll = deepcopy(
            owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_actions"][pending][
                "attack_roll"
            ]
        )
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["hp"] == before["hp"]
        assert rolls == [20]
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(begin) == begun
        completed, completion = act(
            "wight-sword-complete", "complete_srd_pending_action", pending_action_id=pending
        )
        context = completion.result["context"]
        assert context["_attack_roll"] == saved_roll
        assert context["_attack_outcome"] == "critical_hit"
        assert context["_wight_weapon_necrotic_roll"]["results"] == [4, 4]
        assert context["_wight_weapon_necrotic_amount"] == 8
        assert (
            context["_component_1_effective_damage"],
            context["_component_2_effective_damage"],
        ) == (8, 8)
        target = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        assert target["hp"] == before["hp"] - 16 and target["max_hp"] == before["max_hp"]
        assert rolls == [20, 8, 8, 8, 8]
        assert completion.result["result_card"]["title"] == "Attack"
        assert [
            (row["label"], row["value"]) for row in completion.result["result_card"]["summary"]
        ] == [
            ("Outcome", "Critical hit"),
            ("Damage", "Damage 16."),
        ], completion.result["result_card"]
        rows = {row["label"]: row["value"] for row in completion.result["result_card"]["details"]}
        assert "dice 4, 4;" in rows["Wight weapon Necrotic"]
        assert "incoming 8 Necrotic" in rows["Wight weapon Necrotic"]
        assert "8 slashing" in rows["Wight weapon components"]
        assert "8 Necrotic" in rows["Wight weapon components"]
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["attacks_remaining"] == 1
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(completed) == completion
        assert owner.snapshot("casting").state == saved.state
        assert rolls == [20, 8, 8, 8, 8]
