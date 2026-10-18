"""Monk saving-throw proficiency reaches the shared roll and death-save paths."""

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.authority import (
    MechanicContext,
    MechanicsChoiceAnswer,
    MechanicsChoiceResolution,
    owned_payload,
)
from tests.srd_full.test_srd_action_budget import SRD, _mechanic, _run
from tests.srd_full.test_srd_attack_budget import _combat_case


def _save_context(state, data, **parameters):
    """Build the same native save context for decline and accepted replay comparisons."""
    return MechanicContext(
        campaign_id="budget",
        turn_id="turn",
        action_id="disciplined",
        state=owned_payload(state),
        parameters=owned_payload(
            {
                "actor_state": "hero",
                "ability": "constitution",
                "kind": "saving_throw",
                "ability_modifier": 2,
                "proficiency_bonus": 0,
                "proficiency_factor": "none",
                "circumstantial_modifier": 3,
                "target_number": 100,
                **parameters,
            }
        ),
        participants=owned_payload(
            {
                "hero": {
                    "participant_id": "hero",
                    "kind": "player_character",
                    "validation_status": "validated",
                    "package_id": SRD,
                    "data": data,
                }
            }
        ),
    )


@pytest.mark.parametrize(
    "advantage,disadvantage,selection",
    [
        (False, False, "first"),
        (True, False, "first"),
        (True, False, "second"),
        (False, True, "first"),
        (False, True, "second"),
        (True, True, "first"),
    ],
)
def test_replacement_keeps_other_die_and_all_existing_modifiers(
    monkeypatch,
    advantage,
    disadvantage,
    selection,
):
    state, data = _monk()
    state[f"{SRD}.entities"]["hero"].update(exhaustion_level=1, revival_penalty=-1)
    before = deepcopy(state)
    monkeypatch.setattr(dice, "set_seed", lambda _: setattr(dice, "_rng", Random(7)))
    context = _save_context(state, data, advantage=advantage, disadvantage=disadvantage)
    mechanic = _mechanic("d20_test")
    waiting = mechanic(context)
    assert waiting.mutations == () and state == before
    assert [o.option_id for o in waiting.choice.options] == (
        ["keep", "first", "second"] if advantage != disadvantage else ["keep", "first"]
    )
    kept = mechanic(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                waiting.choice.choice_id,
                "keep",
                waiting.choice,
            ),
        )
    )
    original = kept.result["context"]["_d20_test"]
    rerolled = mechanic(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                waiting.choice.choice_id,
                selection,
                waiting.choice,
            ),
        )
    )
    actual = rerolled.result["context"]
    assert actual["_combined_modifier"] == kept.result["context"]["_combined_modifier"] == 7
    assert actual["_d20_test"]["modifier"] == original["modifier"]
    rng = Random(7)
    for _ in original["results"]:
        rng.randint(1, 20)
    replacement = rng.randint(1, 20)
    assert actual["_d20_test"]["results"][0] == replacement
    if len(original["results"]) == 2:
        retained = original["results"][1 if selection == "first" else 0]
        assert actual["_d20_test"]["results"][1] == retained
        expected = (max if advantage else min)(replacement, retained)
    else:
        expected = replacement
        assert replacement < original["total"]
    assert actual["_adjusted_total"] == expected + 7
    assert actual["_outcome"] == "failure" and rerolled.choice is None
    details = {r["label"]: r["value"] for r in rerolled.result["result_card"]["details"]}
    assert details["Original save total"] == kept.result["context"]["_adjusted_total"]
    assert details["Focus spent on reroll"] == 1


@pytest.mark.parametrize("level,total,expected", [(13, 20, 2), (14, 17, 8)])
def test_class_level_earns_feature_and_total_level_only_sets_bonus(level, total, expected):
    state, data = _monk(level)
    data.update(
        level=total,
        classes=[{"id": "monk", "level": level}, {"id": "fighter", "level": total - level}],
    )
    result = _run(
        "d20_test",
        state,
        participant_data=data,
        actor_state="hero",
        ability="constitution",
        kind="saving_throw",
        ability_modifier=2,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=1,
    )
    assert result.result["context"]["_combined_modifier"] == expected


@pytest.mark.parametrize("problem", ["stable", "dead", "positive_hp", "npc"])
def test_ineligible_death_save_rejects_before_any_roll_or_cost(monkeypatch, problem):
    from sylvia_gm.domain.errors import RulesetPreconditionRejected

    state, data = _monk()
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=0, stable=False)
    hero.update(
        {"stable": True}
        if problem == "stable"
        else {"is_alive": False}
        if problem == "dead"
        else {"hp": 1}
        if problem == "positive_hp"
        else {"creature_kind": "monster"}
    )
    before = deepcopy(state)
    monkeypatch.setattr(dice, "roll", lambda *a, **k: pytest.fail("Ineligible save rolled dice"))
    with pytest.raises(RulesetPreconditionRejected):
        _run("death_saving_throw", state, participant_data=data)
    assert state == before


@pytest.mark.parametrize("selection", ["first", "second"])
def test_native_death_disadvantage_rerolls_one_die_and_keeps_modifiers(monkeypatch, selection):
    state, data = _monk()
    state[f"{SRD}.entities"]["hero"].update(
        hp=0,
        stable=False,
        resurrection_tax=True,
        revival_penalty=-1,
        exhaustion_level=1,
        conditions=["unconscious"],
        death_successes=0,
        death_failures=0,
        stable_recovery_hours_remaining=0,
    )
    before = deepcopy(state)
    monkeypatch.setattr(dice, "set_seed", lambda _: setattr(dice, "_rng", Random(2)))
    context = MechanicContext(
        campaign_id="budget",
        turn_id="turn",
        action_id="death",
        state=owned_payload(state),
        parameters=owned_payload({}),
        participants=owned_payload(
            {
                "hero": {
                    "participant_id": "hero",
                    "kind": "player_character",
                    "validation_status": "validated",
                    "package_id": SRD,
                    "data": data,
                }
            }
        ),
    )
    mechanic = _mechanic("death_saving_throw")
    waiting = mechanic(context)
    assert state == before and waiting.mutations == ()
    assert [o.option_id for o in waiting.choice.options] == ["keep", "first", "second"]
    kept = mechanic(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                waiting.choice.choice_id, "keep", waiting.choice
            ),
        )
    )
    rerolled = mechanic(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                waiting.choice.choice_id, selection, waiting.choice
            ),
        )
    )
    original = kept.result["context"]["_death_save"]
    actual = rerolled.result["context"]
    retained = original["results"][1 if selection == "first" else 0]
    assert actual["_death_save"]["results"] == [3, retained]
    assert actual["_death_save"]["total"] == min(3, retained)
    assert actual["_death_save_modifier"] == kept.result["context"]["_death_save_modifier"] == 2
    assert rerolled.choice is None
    details = {r["label"]: r["value"] for r in rerolled.result["result_card"]["details"]}
    assert details["Original save total"] == original["total"] + 2
    assert details["Focus spent on reroll"] == 1


@lru_cache
def _native_archive():
    """Admit a synthetic selection of current native owners; no frozen or release package."""
    import hashlib
    import io
    import json
    import zipfile

    import yaml
    from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

    from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
    from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

    root = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1"
    runtime = resolve_parameter_declarations(
        yaml.load(
            (root / "runtime/ruleset.yaml").read_bytes(),
            Loader=yaml.CSafeLoader,
        )
    )
    public = ["ability_check", "d20_test", "death_saving_throw", "end_combat_turn"]
    selected = set(public)

    def calls(value):
        if isinstance(value, dict):
            if isinstance(value.get("call"), str):
                yield value["call"]
            for child in value.values():
                yield from calls(child)
        elif isinstance(value, list):
            for child in value:
                yield from calls(child)

    pending = list(selected)
    while pending:
        for name in calls(runtime["tools"][pending.pop()]):
            if name not in selected:
                assert name in runtime["tools"], name
                selected.add(name)
                pending.append(name)
    runtime["tools"] = {n: t for n, t in runtime["tools"].items() if n in selected}
    payload = yaml.safe_dump(runtime, sort_keys=False).encode()
    source_record = json.loads((root / "package-record.json").read_text())
    content = json.loads((root / "content/content-declarations.json").read_text())
    content["exclusions"] = []
    content["scope"] = {
        "claim_status": "NOT_COMPLETE",
        "scope_id": "synthetic-native-save-proof",
        "scope_kind": "bounded_package",
        "source_boundary_refs": [],
    }
    content["inclusions"] = [
        {
            "id": "synthetic-native-save",
            "kind": "ruleset_runtime",
            "family": "native-save-proof",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "evidence": {"tests": ["disciplined-survivor-native-reopen"]},
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.d20-test"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": sorted(a["schema_id"] for a in source_record["participant_artifacts"]),
                "state_namespaces": [],
                "tools": public,
                "ui_surfaces": [],
            },
        }
    ]
    files = {"runtime/ruleset.yaml": payload, "content/native.json": canonical_json_bytes(content)}
    draft = {
        "id": SRD,
        "version": source_record["version"],
        "role": "primary",
        "content_kind": "ruleset",
        "content_declarations": "content/native.json",
        "metadata": {"label": "Synthetic current-source native Monk saves"},
        "provenance": {
            "publisher": "sylvia-gm-tests",
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "compatibility": {
            "host_contract": 1,
            "runtime_format": 1,
            "requires": sorted(
                set(source_record["compatibility"]["requires"]) | {"runtime_choice_sequences.v1"}
            ),
        },
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["dnd.srd.5.2.1.d20-test"],
        "tools": [
            {
                "name": n,
                "capability": "dnd.srd.5.2.1.d20-test",
                "input_schema": _tool_schema(runtime["tools"][n], tool_name=n),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
            for n in public
        ],
        "participant_artifacts": source_record["participant_artifacts"],
        "state_schemas": source_record["state_schemas"],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {"path": p, "bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()}
            for p, v in sorted(files.items())
        ],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 4, "packages": [record]}))
        for p, v in files.items():
            archive.writestr(f"packages/{SRD}/{p}", v)
    return stream.getvalue()


def _create_native_campaign(owner, *, death_save=True, campaign_id="disciplined", level=14):
    """Create only selected synthetic data using the exact current native tool closure."""
    import io

    from sylvia_gm.application.authority import CampaignCreate
    from sylvia_gm.domain.authority import StateValue, WriterRole
    from tests.srd_full.test_srd_second_wind import _installed_fighter

    state, data = _monk(level)
    state[f"{SRD}.combat"].update(
        active_actor_kind="character",
        active_actor_can_act=not death_save,
        active_actor_at_zero_hp=death_save,
        active_actor_stable=False,
    )
    state[f"{SRD}.entities"]["hero"].update(
        hp=0 if death_save else 20,
        stable=False,
        death_successes=0,
        death_failures=0,
        conditions=["unconscious", "poisoned"]
        if death_save
        else ["charmed", "frightened", "poisoned"],
        stable_recovery_hours_remaining=0,
    )
    package = owner.install_package_archive(io.BytesIO(_native_archive()), install_id="native")
    actor = _installed_fighter(package.package_lock, fighter=True)
    actor = replace(actor, data={**actor.data, **data})
    owner.create_campaign(
        CampaignCreate(
            campaign_id=campaign_id,
            display_name="Synthetic native save",
            writer=WriterRole.DM,
            package_lock=package.package_lock,
            turn_id="turn",
            participants=(actor,),
            initial_state=tuple(
                StateValue(SRD, key, state[f"{SRD}.{key}"])
                for key in ("combat", "entities", "state")
            ),
        )
    )
    return state, data


@pytest.mark.parametrize(
    "accept,death_save", [(True, False), (False, False), (True, True), (False, True)]
)
def test_current_native_public_owner_reopens_each_answer_and_retries_once(
    tmp_path,
    monkeypatch,
    accept,
    death_save,
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import (
        ActionCommand,
        AuthorityOwner,
        ResolveMechanicsChoiceCommand,
    )
    from sylvia_gm.domain.authority import ActionStatus, WriterRole

    monkeypatch.setattr(dice, "set_seed", lambda _: setattr(dice, "_rng", Random(2)))
    database = tmp_path / "native.db"
    command = ActionCommand(
        campaign_id="disciplined",
        action_id="save",
        idempotency_key="save",
        owner_id="native-owner",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="turn",
        package_id=SRD,
        tool_name="death_saving_throw" if death_save else "d20_test",
        parameters={}
        if death_save
        else {
            "actor_state": "hero",
            "ability": "constitution",
            "kind": "saving_throw",
            "ability_modifier": 2,
            "proficiency_bonus": 0,
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": 100,
        },
    )
    with AuthorityOwner.open(database, "native-owner") as owner:
        owner.set_admin_full_audit(True)
        _create_native_campaign(owner, death_save=death_save)
        baseline = owner.snapshot("disciplined")
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.choice.choice_id == "disciplined-survivor"
        assert owner.snapshot("disciplined") == baseline
        reservation = owner._store.pending_mechanics_action("disciplined")
        assert reservation.action_id == "save" and reservation.basis_revision == 0
        first_answer = ResolveMechanicsChoiceCommand(
            campaign_id="disciplined",
            action_id="save",
            resolution_id="reroll",
            owner_id="native-owner",
            expected_revision=0,
            choice_id=first.choice.choice_id,
            option_id="first" if accept else "keep",
        )
    with AuthorityOwner.open(database, "native-owner") as owner:
        owner.set_admin_full_audit(True)
        assert owner.execute_action(command) == first
        receipt = owner.resolve_mechanics_choice(first_answer)
        if death_save:
            assert (
                receipt.choice.choice_id == "srd-self-restoration" and receipt.choice_sequence == 1
            )
            assert owner.snapshot("disciplined") == baseline
            pending = owner._store.pending_mechanics_action("disciplined")
            assert (pending.action_id, pending.payload_hash, pending.package_sha256) == (
                reservation.action_id,
                reservation.payload_hash,
                reservation.package_sha256,
            )
            assert owner.execute_action(command) == receipt
            assert owner.resolve_mechanics_choice(first_answer) == receipt
            second_answer = replace(
                first_answer,
                resolution_id="restore",
                choice_id=receipt.choice.choice_id,
                option_id="poisoned",
                choice_sequence=1,
            )
    if death_save:
        with AuthorityOwner.open(database, "native-owner") as owner:
            owner.set_admin_full_audit(True)
            assert owner.resolve_mechanics_choice(first_answer) == receipt
            receipt = owner.resolve_mechanics_choice(second_answer)
    with AuthorityOwner.open(database, "native-owner") as owner:
        owner.set_admin_full_audit(True)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("disciplined")
        assert saved.revision == 1 and len(saved.actions) == 1
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["focus_point"] == (13 if accept else 14)
        if death_save:
            assert hero["death_failures"] == 1 and "poisoned" not in hero["conditions"]
            assert saved.state[f"{SRD}.combat"]["turn_active"] is False
        assert owner._store.pending_mechanics_action("disciplined") is None
        assert owner.resolve_mechanics_choice(first_answer) == receipt
        if death_save:
            assert owner.resolve_mechanics_choice(second_answer) == receipt
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("disciplined") == saved
        details = {r["label"]: r["value"] for r in receipt.result["result_card"]["details"]}
        assert details.get("Focus spent on reroll", 0) == int(accept)
        if accept:
            assert details["Original save total"] == (7 if death_save else 9)


def _monk(level=14):
    state, data = _combat_case([{"id": "monk", "level": level}])
    data["saving_throw_proficiencies"] = ["strength", "dexterity"]
    state[f"{SRD}.entities"]["hero"].update(
        name="Monk", resources={"focus_point": level}, resource_maxima={"focus_point": level}
    )
    return state, data


@pytest.mark.parametrize(
    "ability", ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
)
@pytest.mark.parametrize("character_driven", [False, True])
def test_all_saves_gain_proficiency_once_on_both_roll_paths(ability, character_driven):
    state, data = _monk()
    base = (data["abilities"][ability] - 10) // 2
    trained = ability in data["saving_throw_proficiencies"]
    parameters = {"actor": "hero"} if character_driven else {}
    result = _run(
        "d20_test",
        state,
        participant_data=data,
        actor_state="hero",
        ability=ability,
        kind="saving_throw",
        ability_modifier=base if character_driven else base + (5 if trained else 0),
        proficiency_bonus=5 if character_driven else 0,
        proficiency_factor="normal" if character_driven and trained else "none",
        circumstantial_modifier=0,
        target_number=1,
        **parameters,
    )
    assert result.result["context"]["_combined_modifier"] == base + 5
    if not character_driven and not trained:
        details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
        assert details["Disciplined Survivor"] == 5


@pytest.mark.parametrize("level,successes,failures", [(13, 0, 1), (14, 1, 0)])
def test_death_save_adds_earned_proficiency_even_while_unconscious(
    monkeypatch,
    level,
    successes,
    failures,
):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _monk(level)
    state[f"{SRD}.entities"]["hero"].update(
        hp=0,
        stable=False,
        conditions=["unconscious"],
        death_successes=0,
        death_failures=0,
        stable_recovery_hours_remaining=0,
    )
    result = _run("death_saving_throw", state, participant_data=data)
    assert result.result["context"]["_death_save"]["total"] == 5
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["death_successes"] == successes
    assert hero["death_failures"] == failures
    details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
    assert details.get("Disciplined Survivor", 0) == (5 if level == 14 else 0)


@pytest.mark.parametrize(
    "kind,monk_level,expected",
    [
        ("saving_throw", 13, 2),
        ("saving_throw", 14, 7),
        ("ability_check", 14, 2),
        ("attack_roll", 14, 2),
    ],
)
def test_actual_monk_level_and_save_kind_control_the_bonus(kind, monk_level, expected):
    state, data = _monk(monk_level)
    data["classes"].append({"id": "fighter", "level": 1})
    data["level"] += 1
    result = _run(
        "d20_test",
        state,
        participant_data=data,
        actor_state="hero",
        ability="constitution",
        kind=kind,
        ability_modifier=2,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=1,
    )
    assert result.result["context"]["_combined_modifier"] == expected


def test_concentration_uses_the_same_proficiency_without_spending_focus(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _monk()
    state[f"{SRD}.state"]["lasting_tick"] = 0
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(
        concentration_spell_id="bless",
        concentration_expires_tick=20,
        concentration_target_state_id="hero",
        concentration_save_modifier=2,
    )
    result = _run(
        "resolve_srd_concentration_after_damage",
        state,
        participant_data=data,
        caster_state="hero",
        lasting_state="state",
        damage_taken=10,
    )
    assert result.result["context"]["_concentration_save_roll"]["total"] == 5
    assert result.result["context"]["_concentration_save_outcome"] == "success"
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "bless"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 14


@pytest.mark.parametrize(
    "selection,advantage,disadvantage,dc,expected",
    [
        ("keep", False, False, 20, 12),
        ("first", False, False, 20, 26),
        ("first", True, False, 30, 12),
        ("second", True, False, 30, 26),
        ("first", False, True, 20, 10),
        ("second", False, True, 20, 10),
    ],
)
def test_failed_save_offers_one_optional_focus_reroll(
    monkeypatch,
    selection,
    advantage,
    disadvantage,
    dc,
    expected,
):
    state, data = _monk()
    context = MechanicContext(
        campaign_id="budget",
        turn_id="turn",
        action_id="disciplined",
        state=owned_payload(state),
        parameters=owned_payload(
            {
                "actor_state": "hero",
                "ability": "constitution",
                "kind": "saving_throw",
                "ability_modifier": 2,
                "proficiency_bonus": 0,
                "proficiency_factor": "none",
                "circumstantial_modifier": 0,
                "target_number": dc,
                "advantage": advantage,
                "disadvantage": disadvantage,
            }
        ),
        participants=owned_payload(
            {
                "hero": {
                    "participant_id": "hero",
                    "kind": "player_character",
                    "validation_status": "validated",
                    "package_id": SRD,
                    "data": data,
                }
            }
        ),
    )
    monkeypatch.setattr(dice, "set_seed", lambda seed: setattr(dice, "_rng", Random(1)))
    waiting = _mechanic("d20_test")(context)
    assert waiting.choice is not None
    assert waiting.mutations == ()
    assert str(dc) in waiting.choice.prompt
    assert all("{" not in option.label for option in waiting.choice.options)
    result = _mechanic("d20_test")(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                waiting.choice.choice_id, selection, waiting.choice
            ),
        )
    )
    assert result.choice is None
    changed = {f"{m.namespace}.{m.key}": m.value for m in result.mutations}
    hero = changed.get(f"{SRD}.entities", state[f"{SRD}.entities"])["hero"]
    assert hero["resources"]["focus_point"] == (14 if selection == "keep" else 13)
    assert result.result["context"]["_adjusted_total"] == expected
    details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
    assert details.get("Focus spent on reroll", 0) == (0 if selection == "keep" else 1)
    if selection != "keep":
        assert details["Original save total"] == (26 if advantage else 12)


@pytest.mark.parametrize("seed,failures,hp", [(2, 1, 0), (42, 2, 0), (14, 0, 1)])
def test_failed_death_reroll_then_restoration_commit_together(monkeypatch, seed, failures, hp):
    state, data = _monk()
    state[f"{SRD}.entities"]["hero"].update(
        hp=0,
        stable=False,
        death_successes=0,
        death_failures=0,
        conditions=["unconscious", "poisoned"],
        stable_recovery_hours_remaining=0,
    )
    context = MechanicContext(
        campaign_id="budget",
        turn_id="turn",
        action_id="death-reroll",
        state=owned_payload(state),
        parameters=owned_payload({}),
        participants=owned_payload(
            {
                "hero": {
                    "participant_id": "hero",
                    "kind": "player_character",
                    "validation_status": "validated",
                    "package_id": SRD,
                    "data": data,
                }
            }
        ),
    )
    monkeypatch.setattr(dice, "set_seed", lambda _: setattr(dice, "_rng", Random(seed)))
    mechanic = _mechanic("death_saving_throw")
    first = mechanic(context)
    assert first.choice.choice_id == "disciplined-survivor"
    assert first.mutations == ()
    second = mechanic(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                first.choice.choice_id, "first", first.choice
            ),
        )
    )
    if hp == 0:
        assert second.choice is not None
        assert second.mutations == ()
        result = mechanic(
            replace(
                context,
                choice_resolution=MechanicsChoiceResolution(
                    second.choice.choice_id,
                    "poisoned",
                    second.choice,
                    prior_answers=(MechanicsChoiceAnswer("reroll-answer", first.choice, "first"),),
                ),
            )
        )
    else:
        result = second
    assert result.choice is None
    saved = {f"{m.namespace}.{m.key}": m.value for m in result.mutations}
    hero = saved[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["focus_point"] == 13
    assert hero["death_failures"] == failures
    assert hero["death_successes"] == 0
    assert hero["hp"] == hp
    assert ("poisoned" in hero["conditions"]) is (hp > 0)
    assert ("unconscious" in hero["conditions"]) is (hp == 0)
    assert saved[f"{SRD}.combat"]["turn_active"] is (hp > 0)
    details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
    assert details["Focus spent on reroll"] == 1
    assert details["Original save total"] == (7 if seed == 2 else 9)


@pytest.mark.parametrize("reason", ["no_focus", "unearned", "automatic", "foregone"])
def test_reroll_not_offered_without_eligibility_or_an_actual_roll(reason):
    state, data = _monk(13 if reason == "unearned" else 14)
    if reason == "no_focus":
        state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] = 0
    if reason == "automatic":
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["paralyzed"]
    result = _run(
        "d20_test",
        state,
        participant_data=data,
        actor_state="hero",
        ability="strength",
        kind="saving_throw",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=100,
        forego_save=reason == "foregone",
    )
    assert result.choice is None
    assert result.result["context"]["_outcome"] == "failure"
