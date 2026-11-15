"""Autonomous Planar Ally preserves a real prepared creature, paid casting and home return."""

import hashlib
import json
from copy import deepcopy
from math import ceil
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_planar_ally import PROFILE, PUBLIC_TOOLS, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_summoning import SRD, _information_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

ROOT = Path(__file__).resolve().parents[2]
HEADER = {
    "name": "Planar Ally",
    "level": 6,
    "school": "conjuration",
    "classes": ["cleric"],
    "casting_time": "10 minutes",
    "range": "60 feet",
    "components": ["verbal", "somatic"],
    "duration": "Instantaneous",
    "ritual": False,
    "material": "",
    "material_has_cost": False,
    "material_consumed": False,
    "material_parts": [],
    "focus_substitution": False,
}


@pytest.fixture(scope="module")
def planar_runtime():
    from scripts.srd_full.srd_parameter_declarations import compact_runtime, restore_runtime
    from scripts.srd_full.srd_spell_grants import complete_grant_policies, grant_policies

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    base = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert (
        hashlib.sha256(base).hexdigest()
        == "d128bfc9762945dde524e639cf87a9e0fcddd053a96da195ed26cada32cbdb94"
    )
    text = extend_runtime(base.decode())
    assert extend_runtime(text) == text
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"]["planar_ally"] = deepcopy(PROFILE)
    runtime["definitions"]["spell_casting"]["planar_ally"] = deepcopy(HEADER)
    runtime["definitions"]["spell_grants"] = complete_grant_policies(
        grant_policies(), runtime["definitions"]["spell_casting"]
    )
    for tool in runtime["tools"].values():
        for parameter in tool.get("parameters", []):
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | {"planar_ally"})

    class Dumper(yaml.CSafeDumper):
        def ignore_aliases(self, data):
            return True

    expanded = yaml.dump(runtime, Dumper=Dumper, sort_keys=False, allow_unicode=True)
    text = compact_runtime(expanded)
    assert _load_runtime(restore_runtime(text)) == runtime
    assert compact_runtime(text) == text
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="actual-planar-ally")
    return runtime, text.encode()


@pytest.fixture
def planar_owner(monkeypatch, planar_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = planar_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="e" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    return runtime


def _case():
    """Admit printed Air Elemental statistics; no transformation execution certification is used."""
    state, data, _ = _information_case("aid")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, phase="exploration")
    data["spellcasting_grants"] = [
        {"source": "cleric", "ability": "wisdom", "spells": ["planar_ally"]}
    ]
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(prepared_spells=["planar_ally"], inventory=[], spell_materials=[])
    index = json.loads(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/non-beast-form-index.json").read_text()
    )
    row = index["forms"]["air_elemental"]
    assert (
        row["source"]["page"] == 258
        and row["source"]["page_text_sha256"]
        == "51775475d677bd6923bd80aca5f2ef3f4e825525e67f4f5313a974898214347b"
    )
    assert "2d8 + 5" in row["source_stat_block"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="wind_guest",
        name="Zephyr",
        source=(
            "SRD 5.2.1 p258 Air Elemental. "
            "Whirlwind/Air Form not exercised by this service fixture."
        ),
        creature_type="elemental",
        armor_class=row["armor_class"],
        hit_points=row["hit_points"],
        speed=row["walk_speed"],
        size_rank=row["size_rank"],
        initiative_modifier=row["initiative_modifier"],
        proficiency_bonus=3,
        flying_speed=90,
        melee_reach=10,
        multiattack=[{"attack": "Thunderous Slam", "count": 2}],
        damage_immunities=["poison", "thunder"],
        damage_resistances=["bludgeoning", "lightning", "piercing", "slashing"],
        **{ability + "_score": score for ability, score in row["ability_scores"].items()},
        **{ability + "_save": save for ability, save in row["saving_throw_modifiers"].items()},
    )
    # These are already-admitted printed linguistic facts, not a spell-created stat grant.
    state[f"{SRD}.entities"]["wind_guest"]["languages"] = ["primordial_auran"]
    state[f"{SRD}.entities"]["hero"]["languages"] = ["common", "primordial_auran"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    guest = _entity(
        "wind_guest", [60, 40, 0], participant_ref="wind_guest", creature_type="elemental"
    )
    guest.update(footprint={"kind": "box", "half_extents": [5, 5]}, height=10)
    guest["metadata"].update(plane="elemental_air", home_plane="elemental_air")
    scene["entities"].append(guest)
    _run(
        "record_srd_planar_patron",
        state,
        caster_state="hero",
        patron_id="air_court",
        patron_name="The known sovereign of the Air Court",
        loyal_actor_ids=["wind_guest"],
    )
    params = {
        "spell": "planar_ally",
        "caster": "hero",
        "caster_state": "hero",
        "slot_resource": "spell_slot_6",
        "planar_patron": "air_court",
        "planar_ally": "wind_guest",
        "requested_ally_name": "Another wind spirit",
        "planar_ally_position": [15, -10, 0],
        "verbal_component_available": True,
        "somatic_component_available": True,
    }
    return state, data, params


def _appear(state, **changes):
    return _run(
        "cast_spell_planar_ally_service",
        state,
        caster_state="hero",
        planar_patron="air_court",
        planar_ally="wind_guest",
        requested_ally_name="Another wind spirit",
        planar_ally_position=[15, -10, 0],
        **changes,
    )


def _agree(state, **changes):
    args = {
        "caster_state": "hero",
        "ally_state": "wind_guest",
        "decision": "agree",
        "task": "Scout the visible passage.",
        "language": "primordial_auran",
        "agreement_statement": "Zephyr freely accepts this service.",
        "unit": "minute",
        "quantity": 2,
        "agreed_gp": 0,
        "adjustment_reason": "The task serves the Air Court's ethos.",
        "payment_timing": "waived",
    }
    return _run("negotiate_srd_planar_ally", state, **(args | changes))


def _service(state):
    lasting = state[f"{SRD}.state"]
    return lasting["planar_ally_records"][next(iter(lasting["planar_ally_records"]))]


def test_actual_individual_autonomous_deadline_home_return_and_task_completion(planar_owner):
    state, _, _ = _case()
    before = deepcopy(state[f"{SRD}.entities"]["wind_guest"])
    _appear(state)
    body = state[f"{SRD}.entities"]["wind_guest"]
    assert {k: v for k, v in body.items() if k != "planar_ally_service_id"} == before
    assert _service(state)["requested_name"] != _service(state)["actual_name"]
    assert (
        body.get("summon_caster_state_id") is None and body.get("created_caster_state_id") is None
    )
    _agree(state)
    assert _service(state)["agreement"]["suggested_gp"] == 200
    _run("advance_srd_lasting_time", state, elapsed_rounds=19)
    assert _service(state)["active"]
    reopened = json.loads(json.dumps(state))
    _run("advance_srd_lasting_time", reopened, elapsed_rounds=1)
    assert (
        not _service(reopened)["active"] and _service(reopened)["end_reason"] == "service_expired"
    )
    guest = next(
        r for r in reopened[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "wind_guest"
    )
    assert guest["position"] == [60, 40, 0] and guest["metadata"]["plane"] == "elemental_air"
    assert reopened[f"{SRD}.entities"]["wind_guest"] == before | {"planar_ally_service_id": None}
    _appear(reopened)
    _agree(reopened, unit="day", quantity=10, agreed_gp=-1, payment_timing="after")
    newest = reopened[f"{SRD}.state"]["planar_ally_records"]["planar_ally_2"]
    assert newest["agreement"]["suggested_gp"] == 100000 and newest["agreement"]["paid"] is False
    _run(
        "complete_srd_planar_ally_task",
        reopened,
        caster_state="hero",
        ally_state="wind_guest",
        completion_record="Zephyr has completed the GM-resolved scouting task.",
        report="The western passage is clear.",
    )
    assert newest != reopened[f"{SRD}.state"]["planar_ally_records"]["planar_ally_2"]
    assert (
        reopened[f"{SRD}.state"]["planar_ally_records"]["planar_ally_2"]["report"]
        == "The western passage is clear."
    )


def test_no_price_agreement_returns_immediately_without_compelling_the_creature(planar_owner):
    state, _, _ = _case()
    _appear(state)
    tick = state[f"{SRD}.state"]["lasting_tick"]
    _run(
        "negotiate_srd_planar_ally",
        state,
        caster_state="hero",
        ally_state="wind_guest",
        decision="no_agreement",
    )
    assert _service(state)["end_reason"] == "no_agreement"
    assert _service(state)["ended_tick"] == tick and _service(state)["agreement"] is None


@pytest.mark.parametrize(
    "problem",
    [
        "range",
        "occupied_passable",
        "wrong_type",
        "unknown_patron",
        "wrong_language",
        "too_many_days",
    ],
)
def test_actual_source_and_bargain_rejections_are_atomic(planar_owner, problem):
    state, _, _ = _case()
    if problem in {"wrong_language", "too_many_days"}:
        _appear(state)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            _agree(
                state,
                **(
                    {"language": "common"}
                    if problem == "wrong_language"
                    else {"unit": "day", "quantity": 11}
                ),
            )
    else:
        if problem == "wrong_type":
            state[f"{SRD}.entities"]["wind_guest"]["creature_type"] = "humanoid"
        elif problem == "unknown_patron":
            state[f"{SRD}.entities"]["hero"]["planar_known_patrons"] = {}
        elif problem == "occupied_passable":
            row = _entity("other", [15, -10, 0], participant_ref="other", creature_type="humanoid")
            row["movement"] = "passable"
            state[f"{SRD}.state"]["spatial_scene"]["entities"].append(row)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            args = {
                "caster_state": "hero",
                "planar_patron": "air_court",
                "planar_ally": "wind_guest",
                "requested_ally_name": "",
                "planar_ally_position": [60.001, 0, 0] if problem == "range" else [15, -10, 0],
            }
            _run("cast_spell_planar_ally_service", state, **args)
    assert state == before


@pytest.fixture(scope="module")
def planar_archive(planar_runtime):
    from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes

    runtime, encoded = planar_runtime
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    assert not PUBLIC_TOOLS & {t["name"] for t in record["tools"]}
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
    record["tools"].sort(key=lambda t: t["name"])
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "planar-ally-source-owning-fixture",
            "kind": "ruleset_runtime",
            "family": "planar-ally-source-owning-fixture",
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
            "evidence": {"tests": ["actual-planar-ally-paid-cast-service-reopen-return"]},
        }
    )
    content["inclusions"].sort(key=lambda inclusion: inclusion["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def test_public_paid_ten_minute_cast_autonomous_turn_service_reopen_return_and_retry(
    tmp_path,
    monkeypatch,
    planar_owner,
    planar_archive,
):
    import io
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from sylvia_gm.domain.authority import (
        ActionStatus,
        Participant,
        ParticipantValidationStatus,
        StateValue,
        WriterRole,
    )
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _case()
    data.update(
        species="human",
        background="sage",
        size_rank=1,
        saving_throw_proficiencies=["wisdom", "charisma"],
        origin_feats=["magic_initiate"],
    )
    native = deepcopy(state[f"{SRD}.entities"]["wind_guest"])
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = (
        data["abilities"]["dexterity"] - 10
    ) // 2
    _run(
        "prepare_srd_creature",
        state,
        creature_id="wind_witness",
        name="Wind Witness",
        source=native["source"],
        creature_type="elemental",
        armor_class=native["armor_class"],
        hit_points=native["max_hp"],
        speed=native["speed"],
        size_rank=native["size_rank"],
        initiative_modifier=native["initiative_modifier"],
        proficiency_bonus=3,
        flying_speed=90,
        damage_immunities=native["damage_immunities"],
        damage_resistances=native["damage_resistances"],
        melee_reach=10,
        multiattack=deepcopy(native["multiattack"]),
        **{ability + "_score": score for ability, score in native["ability_scores"].items()},
        **{ability + "_save": save for ability, save in native["saving_throw_modifiers"].items()},
    )
    state[f"{SRD}.entities"]["hero"]["planar_known_patrons"] = {}
    record = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/package-record.json").read_text())
    artifact = next(a for a in record["participant_artifacts"] if a["kind"] == "player_character")
    database = tmp_path / "planar-ally.db"
    with AuthorityOwner.open(database, "planar-owner") as owner:
        package = owner.install_package_archive(io.BytesIO(planar_archive), install_id="planar")
        exact = next(e for e in package.package_lock.entries if e.package_id == SRD)
        owner.create_campaign(
            CampaignCreate(
                campaign_id="planar",
                display_name="Planar Ally owning proof",
                writer=WriterRole.DM,
                package_lock=package.package_lock,
                turn_id="planar-turn",
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
                    StateValue(SRD, key.removeprefix(f"{SRD}."), value)
                    for key, value in state.items()
                ),
            )
        )
        mechanics = StatelessMcpMechanics(owner)
        prototype = ActionCommand(
            campaign_id="planar",
            action_id="patron",
            idempotency_key="patron",
            owner_id="planar-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="planar-turn",
            package_id=SRD,
            tool_name="record_srd_planar_patron",
            parameters={},
        )

        def act(identity, name, **choices):
            command = replace(
                prototype,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("planar").revision,
                tool_name=name,
                parameters=choices,
            )
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == receipt
            return command, receipt

        act(
            "known-patron",
            "record_srd_planar_patron",
            caster_state="hero",
            patron_id="air_court",
            patron_name="The known sovereign of the Air Court",
            loyal_actor_ids=["wind_guest"],
        )
        act("cast-begin", "begin_srd_spell_cast", **_begin(parameters))
        begun = owner.snapshot("planar")
        assert begun.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 3
        assert begun.state[f"{SRD}.entities"]["wind_guest"] == native
        act(
            "cast-ten-minutes",
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        cast, cast_receipt = act("paid-appearance", "cast_spell_outside_combat", **parameters)
        saved = owner.snapshot("planar")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_6"] == 2 and hero["extended_casting"] is None
        assert hero["concentration_spell_id"] is None
        ally = saved.state[f"{SRD}.entities"]["wind_guest"]
        assert {k: v for k, v in ally.items() if k != "planar_ally_service_id"} == native
        assert "Autonomous; awaiting agreement" in str(cast_receipt.result["result_card"])
    with AuthorityOwner.open(database, "planar-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("planar").state == saved.state
        assert mechanics.execute_action(cast) == cast_receipt
        agreement, agreement_receipt = act(
            "free-agreement",
            "negotiate_srd_planar_ally",
            caster_state="hero",
            ally_state="wind_guest",
            decision="agree",
            language="primordial_auran",
            task="Scout the passage.",
            agreement_statement="Zephyr freely accepts the aligned service.",
            unit="minute",
            quantity=2,
            agreed_gp=0,
            adjustment_reason="This serves the patron's ethos.",
            payment_timing="waived",
        )
        assert agreement_receipt.result["context"]["_ally_suggested_gp"] == 200
        act(
            "independent-initiative",
            "start_group_combat_encounter",
            combatants=["hero", "wind_guest", "wind_witness"],
        )
        for index in range(3):
            actor = owner.snapshot("planar").state[f"{SRD}.combat"]["current_actor"]
            act(f"begin-{index}", "begin_combat_turn")
            if actor == "wind_guest":
                break
            act(f"end-{index}", "end_combat_turn")
            act(f"next-{index}", "advance_group_combat_encounter_turn")
        else:
            pytest.fail("The autonomous creature did not receive its ordinary independent turn.")
        assert owner.snapshot("planar").state[f"{SRD}.combat"]["action_available"] is True
        assert (
            not owner.snapshot("planar")
            .state[f"{SRD}.entities"]["wind_guest"]
            .get("dodging", False)
        )
        act("ordinary-npc-dodge", "dodge_srd_planar_ally", actor_state="wind_guest")
        assert owner.snapshot("planar").state[f"{SRD}.entities"]["wind_guest"]["dodging"] is True
        act("leave-combat", "end_combat_encounter", end_reason="fighting_stopped")
        current = owner.snapshot("planar")
        service = current.state[f"{SRD}.state"]["planar_ally_records"]["planar_ally_1"]
        remaining = ceil((service["deadline"] - current.state[f"{SRD}.state"]["lasting_tick"]) / 2)
        act("before-deadline", "advance_srd_lasting_time", elapsed_rounds=remaining - 1)
        saved = owner.snapshot("planar")
        assert saved.state[f"{SRD}.state"]["planar_ally_records"]["planar_ally_1"]["active"]
    with AuthorityOwner.open(database, "planar-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("planar").state == saved.state
        assert mechanics.execute_action(agreement) == agreement_receipt
        expired, expiry_receipt = act("real-deadline", "advance_srd_lasting_time", elapsed_rounds=1)
        saved = owner.snapshot("planar")
        service = saved.state[f"{SRD}.state"]["planar_ally_records"]["planar_ally_1"]
        assert service["end_reason"] == "service_expired" and not service["active"]
        guest = next(
            r
            for r in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if r["id"] == "wind_guest"
        )
        assert guest["position"] == [60, 40, 0] and guest["metadata"]["plane"] == "elemental_air"
        assert saved.state[f"{SRD}.entities"]["wind_guest"]["source"] == native["source"]
        assert saved.state[f"{SRD}.entities"]["wind_guest"]["hp"] == native["hp"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        assert mechanics.execute_action(expired) == expiry_receipt
        assert owner.snapshot("planar").state == saved.state
