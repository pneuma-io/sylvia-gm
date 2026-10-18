"""Superior Defense has one earned start-turn choice and shared damage consequences."""

import io
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import (
    ActionStatus,
    MechanicContext,
    MechanicsChoiceResolution,
    StateValue,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.errors import IdempotencyConflict, RulesetPreconditionRejected
from tests.test_srd_action_budget import ROOT as SRD_ROOT
from tests.test_srd_action_budget import SRD, _mechanic, _run
from tests.test_srd_disciplined_survivor import _monk
from tests.test_srd_second_wind import _installed_fighter


@pytest.mark.parametrize("selection,focus,expiry", [("decline", 18, None), ("defend", 15, 20)])
def test_start_turn_offers_defense_without_spending_an_action(selection, focus, expiry):
    state, data = _monk(18)
    state[f"{SRD}.combat"]["turn_active"] = False
    context = MechanicContext(
        campaign_id="budget",
        turn_id="turn",
        action_id="start",
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
    mechanic = _mechanic("begin_combat_turn")
    pending = mechanic(context)
    assert pending.choice.choice_id == "superior-defense"
    assert pending.mutations == ()
    result = mechanic(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                pending.choice.choice_id, selection, pending.choice
            ),
        )
    )
    saved = {f"{m.namespace}.{m.key}": m.value for m in result.mutations}
    hero = saved[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["focus_point"] == focus
    assert hero.get("superior_defense_expires_tick") == expiry
    assert hero["bonus_action_available"] is True
    assert saved[f"{SRD}.combat"]["action_available"] is True


@pytest.mark.parametrize("damage_type,expected", [("fire", 10), ("force", 21)])
def test_shared_damage_resistance_before_temporary_hp(damage_type, expected):
    state, data = _monk(18)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=100, max_hp=100, temp_hp=5, superior_defense_expires_tick=20)
    _run(
        "apply_srd_damage",
        state,
        participant_data=data,
        target="hero",
        amount=21,
        adjustment=0,
        damage_type=damage_type,
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 105 - expected
    assert hero["temp_hp"] == 0


def test_incapacitation_ends_defense_even_after_condition_is_removed():
    state, data = _monk(18)
    state[f"{SRD}.entities"]["hero"].update(
        hp=100, max_hp=100, temp_hp=0, superior_defense_expires_tick=20
    )
    _run("apply_srd_condition", state, participant_data=data, target="hero", condition="stunned")
    _run("remove_srd_condition", state, participant_data=data, target="hero", condition="stunned")
    _run(
        "apply_srd_damage",
        state,
        participant_data=data,
        target="hero",
        amount=21,
        adjustment=0,
        damage_type="fire",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 79


@pytest.mark.parametrize("tick,expected", [(19, 90), (20, 79)])
def test_minute_expiry_does_not_remove_independent_resistances(tick, expected):
    state, data = _monk(18)
    state[f"{SRD}.state"]["lasting_tick"] = tick
    state[f"{SRD}.entities"]["hero"].update(
        hp=100,
        max_hp=100,
        temp_hp=0,
        superior_defense_expires_tick=20,
        damage_resistances=["cold"],
    )
    _run(
        "apply_srd_damage",
        state,
        participant_data=data,
        target="hero",
        amount=21,
        adjustment=0,
        damage_type="fire",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == expected
    _run(
        "apply_srd_damage",
        state,
        participant_data=data,
        target="hero",
        amount=21,
        adjustment=0,
        damage_type="cold",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == expected - 10


def test_composite_damage_checks_each_type_and_does_not_stack_resistance():
    state, data = _monk(18)
    state[f"{SRD}.entities"]["hero"].update(
        hp=100,
        max_hp=100,
        temp_hp=0,
        superior_defense_expires_tick=20,
        damage_resistances=["fire"],
    )
    _run(
        "apply_srd_composite_damage",
        state,
        participant_data=data,
        target="hero",
        component_1_amount=21,
        component_1_damage_type="fire",
        component_2_amount=21,
        component_2_damage_type="force",
        adjustment=0,
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 69


@pytest.mark.parametrize(
    "level,focus,conditions", [(17, 18, []), (18, 2, []), (18, 18, ["stunned"])]
)
def test_start_turn_does_not_offer_unavailable_defense(level, focus, conditions):
    state, data = _monk(level)
    state[f"{SRD}.combat"]["turn_active"] = False
    state[f"{SRD}.entities"]["hero"].update(resources={"focus_point": focus}, conditions=conditions)
    result = _run("begin_combat_turn", state, participant_data=data)
    assert result.choice is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == focus


@lru_cache
def _defense_archive():
    """Admit a synthetic selection of current native owners; no frozen or release package."""
    import hashlib
    import io
    import json
    import zipfile

    import yaml
    from scripts.build_srd_5_2_1_mechanics import _tool_schema

    from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
    from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

    root = SRD_ROOT
    runtime = resolve_parameter_declarations(
        yaml.load(
            (root / "runtime/ruleset.yaml").read_bytes(),
            Loader=yaml.CSafeLoader,
        )
    )
    public = [
        "begin_combat_turn",
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_condition",
        "remove_srd_condition",
        "advance_srd_lasting_time",
        "end_combat_turn",
    ]
    public = sorted(public)
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
        "scope_id": "synthetic-superior-defense-proof",
        "scope_kind": "bounded_package",
        "source_boundary_refs": [],
    }
    content["inclusions"] = [
        {
            "id": "synthetic-superior-defense",
            "kind": "ruleset_runtime",
            "family": "superior-defense-proof",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "evidence": {"tests": ["superior-defense-native-reopen"]},
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
        "metadata": {"label": "Synthetic current-source Superior Defense"},
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


def _start_context(state, data):
    """Use the native turn-start caller with the current actor's validated class data."""
    return MechanicContext(
        campaign_id="budget",
        turn_id="turn",
        action_id="start",
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


def _activate(state, data, option="defend"):
    """Resolve the actual pending start choice and apply only committed mutations."""
    state[f"{SRD}.combat"]["turn_active"] = False
    context = _start_context(state, data)
    mechanic = _mechanic("begin_combat_turn")
    pending = mechanic(context)
    assert pending.choice.choice_id == "superior-defense" and pending.mutations == ()
    result = mechanic(
        replace(
            context,
            choice_resolution=MechanicsChoiceResolution(
                pending.choice.choice_id, option, pending.choice
            ),
        )
    )
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result


@pytest.mark.parametrize(
    "problem",
    [
        "multiclass",
        "other_class",
        "zero_hp",
        "dead",
        "monster",
        "incapacitated",
        "paralyzed",
        "petrified",
        "unconscious",
    ],
)
def test_actual_class_and_living_character_availability(problem):
    state, data = _monk(18)
    hero = state[f"{SRD}.entities"]["hero"]
    if problem == "multiclass":
        data.update(level=20, classes=[{"id": "monk", "level": 17}, {"id": "fighter", "level": 3}])
    elif problem == "other_class":
        data.update(level=20, classes=[{"id": "fighter", "level": 20}])
    elif problem == "zero_hp":
        hero["hp"] = 0
    elif problem == "dead":
        hero["is_alive"] = False
    elif problem == "monster":
        hero["creature_kind"] = "monster"
    else:
        hero["conditions"] = [problem]
    state[f"{SRD}.combat"]["turn_active"] = False
    result = _run("begin_combat_turn", state, participant_data=data)
    assert result.choice is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 18


@pytest.mark.parametrize(
    "option,expected_focus,expected_expiry", [("defend", 12, 24), ("decline", 15, 20)]
)
def test_existing_defense_can_refresh_only_at_a_new_native_turn(
    option, expected_focus, expected_expiry
):
    state, data = _monk(18)
    _activate(state, data)
    _run("advance_srd_lasting_clock", state, elapsed_ticks=4, lasting_state="state")
    _activate(state, data, option)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["focus_point"] == expected_focus
    assert hero["superior_defense_expires_tick"] == expected_expiry
    assert state[f"{SRD}.state"]["superior_defense_target_ids"] == ["hero"]
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _run("begin_combat_turn", state, participant_data=data)
    assert state == before


def test_shared_clock_retires_paid_defense_at_exactly_ten_rounds():
    state, data = _monk(18)
    _activate(state, data)
    _run("end_combat_turn", state, participant_data=data)
    state[f"{SRD}.combat"]["encounter_active"] = False
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert state[f"{SRD}.entities"]["hero"]["superior_defense_expires_tick"] == 20
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["lasting_tick"] == 20
    assert state[f"{SRD}.entities"]["hero"]["superior_defense_expires_tick"] is None
    assert state[f"{SRD}.state"]["superior_defense_target_ids"] == []


@pytest.mark.parametrize(
    "damage,force_resistance,expiry,expected",
    [
        ("single", False, 20, 95),
        ("single", True, 20, 95),
        ("composite", False, 20, 74),
        ("composite", True, 20, 85),
        ("composite", True, 0, 85),
    ],
)
def test_independent_resistances_and_temporary_hp_share_one_damage_packet(
    damage, force_resistance, expiry, expected
):
    state, data = _monk(18)
    resistances = ["fire", "force"] if force_resistance else ["fire"]
    state[f"{SRD}.entities"]["hero"].update(
        hp=100,
        max_hp=100,
        temp_hp=5,
        superior_defense_expires_tick=expiry,
        damage_resistances=resistances,
    )
    if damage == "single":
        _run(
            "apply_srd_damage",
            state,
            participant_data=data,
            target="hero",
            amount=21,
            adjustment=0,
            damage_type="fire",
        )
    else:
        _run(
            "apply_srd_composite_damage",
            state,
            participant_data=data,
            target="hero",
            component_1_amount=21,
            component_1_damage_type="fire",
            component_2_amount=21,
            component_2_damage_type="force",
        )
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == expected and hero["temp_hp"] == 0
    assert hero["damage_resistances"] == resistances


@pytest.mark.parametrize(
    "path",
    [
        "single",
        "composite",
        "explicit",
        "turn_stunned",
        "timed_paralyzed",
        "timed_stunned",
        "timed_incapacitated",
        "timed_petrified",
    ],
)
def test_native_condition_application_retires_defense_before_any_later_consumer(path):
    state, data = _monk(18)
    _activate(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    if path == "single":
        hero["hp"] = 1
        _run("apply_srd_damage", state, target="hero", amount=10, adjustment=0, damage_type="fire")
    elif path == "composite":
        hero["hp"] = 1
        _run(
            "apply_srd_composite_damage",
            state,
            target="hero",
            component_1_amount=10,
            component_1_damage_type="fire",
            component_2_amount=1,
            component_2_damage_type="force",
        )
    elif path == "explicit":
        _run("apply_srd_condition", state, target="hero", condition="incapacitated")
    elif path == "turn_stunned":
        _run(
            "install_srd_turn_effect",
            state,
            caster_state="hero",
            target_state="hero",
            lasting_state="state",
            spell="another_stun",
            effect_label="Stun",
            kind="stunned",
            expiry_owner="caster",
            expiry_phase="start",
            magnitude=0,
            dice_count=0,
            damage_die="d4",
            damage_type="bludgeoning",
        )
    else:
        condition = path.removeprefix("timed_")
        hero[f"timed_condition_unowned_{condition}"] = True
        _run(
            "refresh_srd_timed_condition_target", state, target_state="hero", lasting_state="state"
        )
    assert state[f"{SRD}.entities"]["hero"]["superior_defense_expires_tick"] is None
    if path == "turn_stunned":
        _run("end_combat_turn", state, participant_data=data)
        _run("begin_combat_turn", state, participant_data=data)
    hero = state[f"{SRD}.entities"]["hero"]
    for condition in list(hero["conditions"]):
        if condition != "dead":
            _run("remove_srd_condition", state, target="hero", condition=condition)
    state[f"{SRD}.entities"]["hero"].update(hp=100, max_hp=100, temp_hp=0)
    _run("apply_srd_damage", state, target="hero", amount=21, adjustment=0, damage_type="fire")
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 79


def _create_defense_campaign(
    owner, campaign_id="defense", free_time=False, monk_level=18, delayed=False
):
    """Install source-selected mechanics normally for a synthetic campaign."""
    state, data = _monk(monk_level)
    state[f"{SRD}.combat"].update(turn_active=False, phase="awaiting_turn_start")
    if free_time:
        state[f"{SRD}.combat"]["encounter_active"] = False
    state[f"{SRD}.entities"]["hero"].update(
        hp=100, max_hp=100, temp_hp=5, damage_resistances=["cold", "force"]
    )
    if delayed:
        state[f"{SRD}.entities"]["hero"]["temp_hp"] = 0
        _run(
            "install_srd_turn_effect",
            state,
            participant_data=data,
            caster_state="hero",
            target_state="hero",
            lasting_state="state",
            spell="acid_arrow",
            effect_label="Acid Arrow",
            kind="delayed_damage",
            expiry_owner="target",
            expiry_phase="start",
            magnitude=0,
            dice_count=4,
            damage_die="d4",
            damage_type="acid",
        )
    package = owner.install_package_archive(
        io.BytesIO(_defense_archive()), install_id="native-defense"
    )
    actor = _installed_fighter(package.package_lock, fighter=True)
    actor = replace(actor, data={**actor.data, **data})
    owner.create_campaign(
        CampaignCreate(
            campaign_id=campaign_id,
            display_name="Synthetic Superior Defense",
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


def _defense_action(tool="begin_combat_turn", action_id="start", revision=0, **parameters):
    return ActionCommand(
        campaign_id="defense",
        action_id=action_id,
        idempotency_key=action_id,
        owner_id="test",
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id="turn",
        package_id=SRD,
        tool_name=tool,
        parameters=parameters,
    )


@pytest.mark.parametrize(
    "option,lifecycle,hp",
    [
        ("defend", "active", 95),
        ("decline", "active", 84),
        ("defend", "expired", 84),
        ("defend", "terminated", 84),
    ],
)
def test_public_start_choice_and_later_state_reopen_without_duplicate_focus_cost(
    tmp_path, option, lifecycle, hp
):
    database = tmp_path / "defense.db"
    command = _defense_action()
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        _create_defense_campaign(owner, free_time=lifecycle == "expired")
        baseline = owner.snapshot("defense")
        pending = StatelessMcpMechanics(owner).execute_action(command)
        assert pending.status == "awaiting_choice" and pending.choice_sequence == 0
        assert owner.snapshot("defense") == baseline
        assert "mechanics_choice_answers" not in str(owner.player_projection("defense"))
    answer = ResolveMechanicsChoiceCommand(
        campaign_id="defense",
        action_id="start",
        resolution_id=option,
        owner_id="test",
        expected_revision=0,
        choice_id=pending.choice.choice_id,
        option_id=option,
        choice_sequence=0,
    )
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.execute_action(command) == pending
        receipt = owner.resolve_mechanics_choice(answer)
        assert receipt.status is ActionStatus.COMMITTED
        committed = owner.snapshot("defense")
        hero = committed.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["focus_point"] == (15 if option == "defend" else 18)
        assert hero["bonus_action_available"] is True
        assert committed.state[f"{SRD}.combat"]["action_available"] is True
        assert committed.revision == 1 and len(committed.actions) == 1
        assert owner._store.pending_mechanics_action("defense") is None
        if option == "defend":
            summary = {r["label"]: r["value"] for r in receipt.result["result_card"]["summary"]}
            assert summary["Superior Defense"] == "3 Focus spent; resistance active"
        else:
            assert receipt.result.get("result_card") is None
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.execute_action(command) == receipt
        assert owner.resolve_mechanics_choice(answer) == receipt
        assert owner.snapshot("defense") == committed
        if lifecycle == "expired":
            owner.execute_action(_defense_action("end_combat_turn", "end", 1))
            clock = _defense_action("advance_srd_lasting_time", "time", 2, elapsed_rounds=10)
            owner.execute_action(clock)
        elif lifecycle == "terminated":
            condition = _defense_action(
                "apply_srd_condition", "condition", 1, target="hero", condition="stunned"
            )
            owner.execute_action(condition)
            owner.execute_action(
                _defense_action(
                    "remove_srd_condition", "remove", 2, target="hero", condition="stunned"
                )
            )
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        saved = owner.snapshot("defense")
        defense = saved.state[f"{SRD}.entities"]["hero"].get("superior_defense_expires_tick")
        assert (defense is not None) is (option == "defend" and lifecycle == "active")
        damage = _defense_action(
            "apply_srd_damage",
            "damage",
            saved.revision,
            target="hero",
            amount=21,
            adjustment=0,
            damage_type="fire",
        )
        damage_receipt = owner.execute_action(damage)
        after_damage = owner.snapshot("defense")
        assert after_damage.state[f"{SRD}.entities"]["hero"]["hp"] == hp
        assert after_damage.state[f"{SRD}.entities"]["hero"]["damage_resistances"] == [
            "cold",
            "force",
        ]
        assert owner.execute_action(damage) == damage_receipt
        assert owner.resolve_mechanics_choice(answer) == receipt
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("defense") == after_damage
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("defense") == after_damage


@pytest.mark.parametrize("option", ["unearned", "decline", "defend"])
@pytest.mark.parametrize("delayed", [False, True])
def test_public_turn_card_preserves_routine_and_meaningful_results(tmp_path, option, delayed):
    database = tmp_path / "paired-cards.db"
    command = _defense_action()
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        _create_defense_campaign(
            owner, monk_level=17 if option == "unearned" else 18, delayed=delayed
        )
        baseline = owner.snapshot("defense")
        pending = owner.execute_action(command)
        if option != "unearned":
            assert pending.status == "awaiting_choice"
            assert owner.snapshot("defense") == baseline
    answer = None
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.execute_action(command) == pending
        if option != "unearned":
            answer = ResolveMechanicsChoiceCommand(
                campaign_id="defense",
                action_id="start",
                resolution_id=option,
                owner_id="test",
                expected_revision=0,
                choice_id=pending.choice.choice_id,
                option_id=option,
                choice_sequence=0,
            )
            with pytest.raises(IdempotencyConflict):
                owner.resolve_mechanics_choice(
                    replace(answer, expected_revision=1, resolution_id="stale")
                )
            assert owner.snapshot("defense") == baseline
            assert owner.execute_action(command) == pending
            receipt = owner.resolve_mechanics_choice(answer)
        else:
            receipt = pending
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("defense")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        initial_focus = baseline.state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"]
        assert hero["resources"]["focus_point"] == initial_focus - (3 if option == "defend" else 0)
        assert hero["bonus_action_available"] is True
        assert saved.state[f"{SRD}.combat"]["action_available"] is True
        assert owner._store.pending_mechanics_action("defense") is None
        card = receipt.result.get("result_card")
        if option == "defend" or delayed:
            assert card is not None
            summary = {row["label"]: row["value"] for row in card["summary"]}
            if option == "defend":
                assert summary["Superior Defense"] == "3 Focus spent; resistance active"
                assert hero["superior_defense_expires_tick"] == 20
            else:
                assert "Superior Defense" not in summary
            if delayed:
                assert summary["Delayed spell effects"] == 1
                assert summary["Damage applied"] == 100 - hero["hp"] > 0
                assert any("Acid Arrow" in row["value"] for row in card["details"])
        else:
            assert card is None
            assert receipt.result["activity_label"] == "Start combat turn"
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.execute_action(command) == receipt
        if answer is not None:
            assert owner.resolve_mechanics_choice(answer) == receipt
        assert owner.snapshot("defense") == saved
