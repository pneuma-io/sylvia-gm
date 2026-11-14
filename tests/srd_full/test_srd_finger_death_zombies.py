"""One paid spell kill, ordinary next-turn entrant and saved native verbal-order journey."""

import io
import json
from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest
from scripts.srd_full.srd_finger_death_zombies import PUBLIC_TOOLS, extend_runtime
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
from tests.srd_full.test_srd_native_wight_rise import (
    DEPENDENCIES,
    ROOT,
    _fresh_native_attack_window,
    _victim,
    rise_owner,
)
from tests.srd_full.test_srd_spell_summoning import SRD, _free
from tests.srd_full.test_srd_spell_wave3_information import _case


@pytest.fixture(scope="module")
def finger_runtime():
    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = restore_runtime((DEPENDENCIES / "wight-public-parent-runtime.yaml").read_text())
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    text = _fresh_native_attack_window(text)
    runtime = _load_runtime(text)
    text = compact_runtime(text)
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="finger-death-zombies")
    return runtime, text.encode()


@pytest.fixture
def finger_owner(monkeypatch, finger_runtime):
    return rise_owner.__wrapped__(monkeypatch, finger_runtime)


@pytest.fixture(scope="module")
def finger_archive(finger_runtime):
    from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes

    runtime, encoded = finger_runtime
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        name = declaration["name"]
        declaration["input_schema"] = _tool_schema(runtime["tools"][name], tool_name=name)
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
    record["tools"].sort(key=lambda d: d["name"])
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "finger-death-owning-fixture",
            "kind": "ruleset_runtime",
            "family": "finger-death-owning-fixture",
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
            "evidence": {"tests": ["paid-finger-death-next-turn-native-zombie"]},
        }
    )
    content["inclusions"].sort(key=lambda inclusion: inclusion["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def _campaign(owner, archive):
    """Reuse a validated synthetic Wizard and exact prepared Commoner receiving bodies."""
    package = owner.install_package_archive(io.BytesIO(archive), install_id="casting")
    exact = next(entry for entry in package.package_lock.entries if entry.package_id == SRD)
    record = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/package-record.json").read_text())
    artifact = next(a for a in record["participant_artifacts"] if a["kind"] == "player_character")
    state, data, parameters = _case("finger_of_death")
    _free(state)
    for identity in ("guard", "other"):
        state[f"{SRD}.entities"].pop(identity)
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    scene[:] = [e for e in scene if e["id"] not in {"guard", "orb"}]
    for identity, x in (("guard", 20), ("other", 30), ("bystander", 40)):
        _victim(state, identity)
        scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
        scene[-1]["position"] = [x, 0, 0]
    data.update(
        species="human",
        background="sage",
        size_rank=1,
        saving_throw_proficiencies=["intelligence", "wisdom"],
        origin_feats=["magic_initiate"],
    )
    state[f"{SRD}.entities"]["hero"].update(
        creature_type="humanoid",
        initiative_modifier=(data["abilities"]["dexterity"] - 10) // 2,
    )
    owner.create_campaign(
        CampaignCreate(
            campaign_id="casting",
            display_name="Finger of Death native consequence",
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
                StateValue(SRD, k.removeprefix(f"{SRD}."), v) for k, v in state.items()
            ),
        )
    )
    return parameters


def test_paid_kill_next_caster_turn_real_entrant_verbal_slam_reopen_and_retry(
    tmp_path,
    monkeypatch,
    finger_owner,
    finger_archive,
):
    monkeypatch.setattr(dice, "_rng", Random(5))
    database = tmp_path / "finger-death.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        parameters = _campaign(owner, finger_archive)
        prototype = ActionCommand(
            campaign_id="casting",
            action_id="finger-kill",
            idempotency_key="finger-kill",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
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
            receipt = StatelessMcpMechanics(owner).execute_action(action)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(action) == receipt
            return action, receipt

        killed, kill_receipt = act("finger-kill", "cast_spell_outside_combat", **parameters)
        saved = owner.snapshot("casting")
        corpse = saved.state[f"{SRD}.entities"]["guard"]
        assert corpse["is_alive"] is False and corpse["death_creature_type"] == "humanoid"
        rise_id = corpse["finger_death_rise_id"]
        pending = saved.state[f"{SRD}.state"]["finger_death_rise_records"][rise_id]
        assert pending["active"] is True and pending["zombie"] is None
        assert (
            pending["caster_turn"]
            == saved.state[f"{SRD}.entities"]["hero"].get("turns_started", 0) + 1
        )
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        act(
            "finger-language",
            "admit_srd_finger_death_zombie_language",
            caster_state="hero",
            corpse_state="guard",
            other_language="draconic",
        )
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(killed) == kill_receipt
        act(
            "finger-group",
            "start_group_combat_encounter",
            combatants=["hero", "other", "bystander"],
        )
        for index in range(4):
            identity = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            began, begin_receipt = act(f"finger-begin-{index}", "begin_combat_turn")
            if identity == "hero":
                break
            act(f"finger-end-{index}", "end_combat_turn")
            act(f"finger-next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The real caster never reached its next turn.")
        saved = owner.snapshot("casting")
        pending = saved.state[f"{SRD}.state"]["finger_death_rise_records"][rise_id]
        identity = pending["zombie"]
        assert pending["status"] == "risen" and pending["active"] is False
        actor = saved.state[f"{SRD}.entities"][identity]
        assert actor["created_spell_id"] == "finger_of_death"
        assert actor["created_caster_state_id"] == "hero" and actor["created_form_id"] == "zombie"
        assert actor["summon_active"] is False and actor.get("animated_control_until") is None
        assert actor["created_understood_languages"] == ["common", "draconic"]
        order = saved.state[f"{SRD}.combat"]["order"]
        entrant = next(e for e in order if e["entity_id"] == identity)
        assert (
            entrant["initiative_result"] == entrant["roll"]["total"] + actor["initiative_modifier"]
        )
        assert sum(e["entity_id"] == identity for e in order) == 1
        assert saved.state[f"{SRD}.combat"]["current_actor"] == "hero"
        assert order[saved.state[f"{SRD}.combat"]["turn_index"]]["entity_id"] == "hero"
        assert saved.state[f"{SRD}.entities"]["guard"]["body_animated_actor_id"] == identity
        assert saved.state[f"{SRD}.entities"]["guard"]["body_available"] is False
        act(
            "finger-verbal",
            "command_srd_finger_death_zombie",
            caster_state="hero",
            zombie_state=identity,
            action="attack",
            language="common",
        )
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(began) == begin_receipt
        assert owner.snapshot("casting").state == saved.state
        for index in range(5):
            act(f"finger-zombie-end-{index}", "end_combat_turn")
            act(f"finger-zombie-next-{index}", "advance_group_combat_encounter_turn")
            current = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            act(f"finger-zombie-begin-{index}", "begin_combat_turn")
            if current == identity:
                break
        else:
            pytest.fail("The genuine ordinary Zombie did not receive its own turn.")
        dice._rng.seed(5)
        _, attack = act(
            "finger-slam",
            "begin_srd_creature_attack",
            target="other",
            attack_name="Slam",
            attack_modifier=3,
            attack_mode="melee",
            damage_expression="d8",
            damage_modifier=1,
            damage_type="bludgeoning",
            normal_range=5,
            long_range=5,
            attack_distance=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
            use_multiattack=False,
            attack_uses_weapon=False,
        )
        pending_id = attack.result["context"]["_pending_action_id"]
        completed, hit = act(
            "finger-slam-complete",
            "complete_srd_pending_action",
            pending_action_id=pending_id,
        )
        assert hit.result["context"]["_attack_outcome"] in {"hit", "critical_hit"}
        assert hit.result["context"]["_damage_applied"] > 0
        saved = owner.snapshot("casting")
        assert len(saved.state[f"{SRD}.state"]["finger_death_rise_ids"]) == 1
        assert sum(e["entity_id"] == identity for e in saved.state[f"{SRD}.combat"]["order"]) == 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(completed) == hit
        assert owner.execute_action(killed) == kill_receipt
        assert owner.snapshot("casting").state == saved.state
