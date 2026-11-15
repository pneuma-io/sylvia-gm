"""Material costs use saved carried supplies and the ordinary transactional cast."""

from copy import deepcopy

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.session import SessionMessageKind
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _casting_headers, _spell_case


def _materials_case(spell="stoneskin", *, quantity=2, value_cp=10000, kind="specific"):
    state, data, parameters = _spell_case(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"] = ["diamond_dust"]
    hero["spell_materials"] = [
        {
            "id": "dust",
            "inventory_item": "diamond_dust",
            "kind": kind,
            "components": [f"{spell}.1"],
            "quantity": quantity,
            "value_cp": value_cp,
        }
    ]
    parameters.update(material_component_available=True, material_item_ids=["dust"])
    if spell == "chromatic_orb":
        parameters["selected_damage_type"] = "fire"
    return state, data, parameters


def test_consumed_material_decreases_saved_supply_and_names_the_cost():
    state, data, parameters = _materials_case()
    result = _run("cast_spell", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["spell_materials"][0]["quantity"] == 1
    assert hero["inventory"] == ["diamond_dust"]
    assert "consumed" in str(result.result["result_card"]["summary"])


@pytest.mark.parametrize("problem", ["value", "quantity", "not_carried", "wrong_component", "hand"])
def test_missing_material_requirement_rejects_without_spending_any_cost(problem):
    state, data, parameters = _materials_case()
    hero = state[f"{SRD}.entities"]["hero"]
    if problem == "value":
        hero["spell_materials"][0]["value_cp"] = 9999
    elif problem == "quantity":
        hero["spell_materials"][0]["quantity"] = 0
    elif problem == "not_carried":
        hero["inventory"] = []
    elif problem == "wrong_component":
        hero["spell_materials"][0]["components"] = ["chromatic_orb.1"]
    else:
        parameters["material_component_available"] = False
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError), match="[Mm]aterial"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def test_reusable_priced_component_is_retained_and_pouch_cannot_replace_it():
    state, data, parameters = _materials_case("chromatic_orb", value_cp=5000, quantity=1)
    before = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    _run(
        "cast_spell",
        state,
        participant_data=data,
        **(parameters | {"selected_damage_type": "fire"}),
    )
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"] == before
    state, data, parameters = _materials_case("chromatic_orb", kind="component_pouch")
    state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["components"] = []
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError), match="[Mm]aterial"):
        _run("cast_spell", state, participant_data=data, **parameters)


def test_pouch_substitutes_for_ordinary_material_without_consumption():
    state, data, parameters = _materials_case("acid_arrow", kind="component_pouch", value_cp=0)
    state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["components"] = []
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2


def test_removing_other_inventory_keeps_carried_materials_usable_and_preserves_stock():
    state, data, parameters = _materials_case("acid_arrow", kind="component_pouch", value_cp=0)
    hero = state[f"{SRD}.entities"]["hero"]
    missing = {
        "id": "lost", "inventory_item": "lost_dust", "kind": "specific",
        "components": ["stoneskin.1"], "quantity": 1, "value_cp": 10000,
    }
    hero["inventory"].append("lost_dust")
    hero["spell_materials"].append(missing)
    _run("set_srd_carried_inventory", state, actor_state="hero", inventory=["diamond_dust"])
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][1] == missing

    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Material"):
        _run(
            "resolve_srd_spell_materials", state, caster_state="hero", spell="stoneskin",
            item_ids=["lost"], materials_available=True, components_waived=False,
            target_count=1, permitted_focus_kinds=[],
        )
    assert state == before


def test_last_material_unit_leaves_inventory_and_failed_cast_restores_it():
    state, data, parameters = _materials_case(quantity=1)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "cast_spell",
            state,
            participant_data=data,
            **(
                parameters
                | {
                    "target_distance": 60,
                    "caster_can_touch_target": False,
                }
            ),
        )
    assert state == before
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 0
    assert state[f"{SRD}.entities"]["hero"]["inventory"] == []


@pytest.mark.parametrize("granted", [True, False])
def test_focus_substitution_uses_the_actual_granting_class(granted):
    state, data, parameters = _materials_case("acid_arrow", kind="arcane_focus", value_cp=0)
    state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["components"] = []
    if not granted:
        data["classes"] = [{"id": "bard", "level": 17}]
        data["spellcasting_grants"] = [
            {"source": "bard", "ability": "charisma", "spells": ["acid_arrow"]},
        ]
    if granted:
        _run("cast_spell", state, participant_data=data, **parameters)
    else:
        with pytest.raises(RulesetPreconditionRejected, match="Material"):
            _run("cast_spell", state, participant_data=data, **parameters)


def test_public_inventory_component_count_is_consumed_once_and_survives_reopen(tmp_path):
    database = tmp_path / "materials.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "stoneskin")
        before = owner.snapshot("casting")
        hero = before.state[f"{SRD}.entities"]["hero"]
        materials = [dict(item) for item in hero["spell_materials"]]
        materials[0]["quantity"] = 1
        session = owner.create_agent_session(
            campaign_id="casting",
            role=WriterRole.DM,
            provider_id="scripted",
            model_id="scripted",
        )
        owner.append_agent_message(
            session_id=session.session_id,
            message_id="supplies",
            kind=SessionMessageKind.USER,
            content={"text": "I count my diamond dust: one measure remains."},
        )
        mcp = StatelessMcpMechanics(owner)
        reply = mcp.commit_campaign_file(
            "casting",
            session_id=session.session_id,
            basis_message_id="supplies",
            parameters={
                "state_change": "changed",
                "state_change_summary": "Recorded the remaining dust.",
                "character_updates": [
                    {
                        "participant_id": "hero",
                        "expected_revision": before.revision,
                        "fields": {"inventory": hero["inventory"], "spell_materials": materials},
                    }
                ],
            },
        )
        assert reply["status"] == "campaign_file_updated"
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 0
        assert (
            materials[0]["inventory_item"]
            not in saved.state[f"{SRD}.entities"]["hero"]["inventory"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        with pytest.raises(MechanicsRejected, match="Material"):
            _act(owner, "cast_spell_outside_combat", **parameters)
        assert owner.snapshot("casting").state == saved.state


def test_npc_explicit_material_waiver_uses_saved_exception_without_inventing_supplies(tmp_path):
    with AuthorityOwner.open(tmp_path / "npc-material.db", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "stoneskin")
        receipt, _ = _prepare_mage(
            owner,
            [{"spell": "stoneskin", "uses_per_day": 1}],
            spell_component_exemptions=["material"],
        )
        assert receipt.status is ActionStatus.COMMITTED
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters.update(
            caster_state="mage", material_component_available=False, material_item_ids=[]
        )
        receipt, _ = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        mage = owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]
        assert mage["spell_materials"] == [] and mage["inventory"] == []
        assert mage["spell_uses_remaining"]["stoneskin"] == 0


@pytest.mark.parametrize(
    "spell,target_count,remaining",
    [
        ("clone", 1, [2, 3]),
        ("astral_projection", 2, [1, 1]),
    ],
)
def test_shared_material_parts_preserve_mixed_consumption_and_scale_per_target(
    spell,
    target_count,
    remaining,
):
    # These spells do not yet have public effect families. This proves their shared
    # component primitive only, including rollback when a later component is absent.
    state, _, _ = _materials_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["spell_materials"] = [
        {
            "id": f"part_{index}",
            "inventory_item": f"item_{index}",
            "kind": "specific",
            "quantity": 3,
            "value_cp": part["minimum_value_cp"],
            "components": [part["id"]],
        }
        for index, part in enumerate(_casting_headers()[spell]["material_parts"])
    ]
    hero["inventory"] = [supply["inventory_item"] for supply in hero["spell_materials"]]
    initial = deepcopy(state)
    arguments = {
        "caster_state": "hero", "spell": spell, "item_ids": ["part_0", "part_1"],
        "materials_available": True, "components_waived": False,
        "target_count": target_count, "permitted_focus_kinds": [],
    }
    _run("resolve_srd_spell_materials", state, **arguments)
    assert [
        supply["quantity"] for supply in state[f"{SRD}.entities"]["hero"]["spell_materials"]
    ] == remaining
    initial[f"{SRD}.entities"]["hero"]["spell_materials"][1]["quantity"] = 0
    before = deepcopy(initial)
    with pytest.raises(RulesetPreconditionRejected, match="Material"):
        _run("resolve_srd_spell_materials", initial, **arguments)
    assert initial == before
