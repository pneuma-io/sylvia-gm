"""Circle source receipts use the real clock, permanent anchors and paid cast path."""

import json
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache

import pytest
import yaml
from scripts import srd_spell_travel as travel
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_travel_circle import CIRCLE_CAST_CHOICES, bind_circle_consumers, circle_tools
from scripts.srd_full.srd_timed_actor_defenses import _call, _tool, _when

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests import test_srd_spell_travel_teleport_objects as objects
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _case


@lru_cache
def _runtime():
    # This is the previous immutable paid scope plus only the new Circle source tools/hooks.
    runtime = deepcopy(objects._runtime()[0])
    tools = runtime["tools"]
    changed = (
        circle_tools()
        | travel._casting_tool()
        | {
            "study_srd_circle_sigil": travel._traversal_tools()["study_srd_circle_sigil"],
            "enter_srd_teleportation_circle": travel._traversal_tools()[
                "enter_srd_teleportation_circle"
            ],
        }
    )
    travel._normalize(changed)
    travel._wrap(changed)
    tools.update(changed)
    movement = {}
    bind_circle_consumers(tools, movement)
    travel._normalize(movement)
    tools.update(movement)
    names = {"cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"}
    for name in names:
        tool = tools[name]
        existing = {p["name"] for p in tool["parameters"]}
        tool["parameters"].extend(
            deepcopy([p for p in CIRCLE_CAST_CHOICES if p["name"] not in existing])
        )
        for call in objects._calls(tool["effects"]):
            if call["call"] in names | {"cast_spell_travel_spell"}:
                call["params"].update(
                    {p["name"]: "{" + p["name"] + "}" for p in CIRCLE_CAST_CHOICES}
                )
    cast = tools["cast_spell"]
    boundary = next(
        i for i, e in enumerate(cast["effects"]) if e.get("call") == "resolve_srd_spell_materials"
    )
    cast["effects"].insert(
        boundary,
        _when(
            "spell == 'teleportation_circle'",
            [
                _call(
                    "validate_srd_circle_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "destination_subject": "{travel_destination_subject}",
                        "point": "{travel_destination}",
                        "depiction_depth": "{circle_depiction_depth}",
                    },
                )
            ],
        ),
    )
    cast["effects"] = _tool("", cast["parameters"], cast["effects"])["effects"]
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def circle_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    original = bundled_packages._srd_drafts

    def declared():
        primary, reference = original()
        record = deepcopy(primary.record)
        payload = dict(primary.payloads)
        name = "learn_srd_initial_circle_sigils"
        if name not in {r["name"] for r in record["tools"]}:
            record["tools"].append(
                {
                    "name": name,
                    "capability": "dnd.srd.5.2.1.spells",
                    "input_schema": _tool_schema(_runtime()[0]["tools"][name], tool_name=name),
                    "writers": ["dm"],
                    "turn_statuses": ["open"],
                    "state_predicates": [],
                }
            )
            declarations = json.loads(payload["content/content-declarations.json"])
            for inclusion in declarations["inclusions"]:
                if "study_srd_circle_sigil" in inclusion.get("links", {}).get("tools", []):
                    inclusion["links"]["tools"] = sorted(set(inclusion["links"]["tools"]) | {name})
            payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", declared)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _circle_case(_spell="teleportation_circle"):
    state, data, params = _case(
        "teleportation_circle", travel_destination_subject="orb", travel_destination=[0, 0, 0]
    )
    params.pop("targets", None)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    first = next(row for row in scene["entities"] if row["id"] == "orb")
    first.update(position=[100, 0, 0], movement="passable", visibility="clear")
    first["metadata"].update(teleportation_circle=True, sigil="amber", lighting="bright")
    second = deepcopy(first)
    second.update(id="second-circle", position=[200, 0, 0])
    second["metadata"]["sigil"] = "blue"
    scene["entities"].append(second)
    third = deepcopy(first)
    third.update(id="study-circle", position=[103, 0, 0])
    third["metadata"]["sigil"] = "copper"
    scene["entities"].append(third)
    return state, data, params


def _install(state, point=(0, 0, 0)):
    return budget._run(
        "cast_spell_travel_spell",
        state,
        caster_state="hero",
        spell="teleportation_circle",
        slot_level=5,
        concentration_modifier=0,
        all_targets_willing=True,
        material_item_ids=[],
        travel_destination=list(point),
        travel_destination_subject="orb",
    )


def test_daily_installer_receipts_count_real_days_and_create_one_usable_permanent_anchor(
    circle_scope,
):
    # The installer boundary follows shared payment; this proves its receipts, not365 paid journeys.
    state, _, _ = _circle_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["known_circle_sigils"] = ["amber"]
    _install(state)
    _install(state)
    assert state[f"{SRD}.state"]["travel_circles"][-1]["consecutive_days"] == 1
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    _install(state)
    assert state[f"{SRD}.state"]["travel_circles"][-1]["consecutive_days"] == 2
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=28800)
    _install(state)
    assert state[f"{SRD}.state"]["travel_circles"][-1]["consecutive_days"] == 1
    _install(state, (5, 0, 0))
    assert state[f"{SRD}.state"]["travel_circles"][-1]["consecutive_days"] == 1
    for _day in range(1, 365):
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
        _install(state, (5, 0, 0))
    lasting = state[f"{SRD}.state"]
    record = lasting["travel_circles"][-1]
    assert record["consecutive_days"] == 365 and record["permanent"] is True
    permanent_id = record["permanent_circle_id"]
    circle = next(r for r in lasting["spatial_scene"]["entities"] if r["id"] == permanent_id)
    assert circle["footprint"] == {"kind": "circle", "radius": 5}
    assert circle["position"] == [5, 0, 0] and circle["movement"] == "passable"
    assert circle["metadata"]["teleportation_circle"] is True
    budget._run("study_srd_circle_sigil", state, actor_state="hero", circle_subject=permanent_id)
    assert circle["metadata"]["sigil"] in state[f"{SRD}.entities"]["hero"]["known_circle_sigils"]
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=28800)
    _install(state, (5, 0, 0))
    lasting = state[f"{SRD}.state"]
    assert lasting["travel_circles"][-1]["permanent_circle_id"] == permanent_id
    assert len([r for r in lasting["spatial_scene"]["entities"] if r["id"] == permanent_id]) == 1


def test_initial_choices_validate_actual_known_grant_once_independently_of_preparation(
    circle_scope,
):
    state, data, _ = _circle_case()
    data["prepared_spells"] = []
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="two different"):
        budget._run(
            "learn_srd_initial_circle_sigils",
            state,
            participant_data=data,
            caster="hero",
            caster_state="hero",
            circle_subjects=["orb", "orb"],
        )
    assert state == before
    budget._run(
        "learn_srd_initial_circle_sigils",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        circle_subjects=["orb", "second-circle"],
    )
    assert state[f"{SRD}.entities"]["hero"]["known_circle_sigils"] == ["amber", "blue"]
    with pytest.raises(RulesetPreconditionRejected, match="learned once"):
        budget._run(
            "learn_srd_initial_circle_sigils",
            state,
            participant_data=data,
            caster="hero",
            caster_state="hero",
            circle_subjects=["orb", "second-circle"],
        )


def test_real_clock_plane_return_interrupts_study_without_granting_partial_sigil(circle_scope):
    state, data, params = _case("etherealness")
    circle = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    circle.update(position=[5, 0, 0], movement="passable", visibility="clear")
    circle["metadata"].update(
        teleportation_circle=True,
        sigil="sunset",
        plane="ethereal",
        lighting="bright",
    )
    budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=4791)
    before = state[f"{SRD}.state"]["lasting_tick"]
    budget._run("study_srd_circle_sigil", state, actor_state="hero", circle_subject="orb")
    hero = state[f"{SRD}.entities"]["hero"]
    assert state[f"{SRD}.state"]["lasting_tick"] == before + 20
    assert hero.get("known_circle_sigils") is None and hero["circle_study"] is None
    body = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero")
    assert body["metadata"]["plane"] == "material"


def test_paid_minute_circle_initial_choices_study_reopen_and_retry(
    circle_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _circle_case)
    db = tmp_path / "circle.db"
    with AuthorityOwner.open(db, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "teleportation_circle")
        mechanics = StatelessMcpMechanics(owner)
        base = ActionCommand(
            campaign_id="casting",
            action_id="circle",
            idempotency_key="circle",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters={},
        )

        def act(name, tool, facts):
            cmd = replace(
                base,
                action_id=name,
                idempotency_key=name,
                tool_name=tool,
                expected_revision=owner.snapshot("casting").revision,
                parameters=facts,
            )
            receipt = mechanics.execute_action(cmd)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            saved = owner.snapshot("casting")
            assert mechanics.execute_action(cmd) == receipt and owner.snapshot("casting") == saved
            return cmd, saved

        act(
            "learn",
            "learn_srd_initial_circle_sigils",
            {
                "caster": "hero",
                "caster_state": "hero",
                "circle_subjects": ["orb", "second-circle"],
            },
        )
        act("begin", "begin_srd_spell_cast", _begin(params))
        act(
            "minute",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        ready = owner.snapshot("casting")
        wrong = replace(
            base,
            action_id="wrong",
            idempotency_key="wrong",
            expected_revision=ready.revision,
            parameters=params | {"travel_destination": [11, 0, 0]},
        )
        with pytest.raises(MechanicsRejected, match="ten feet"):
            mechanics.execute_action(wrong)
        assert owner.snapshot("casting") == ready
        cast, saved = act("paid", "cast_spell_outside_combat", params)
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_5"] == 2
        assert saved.state[f"{SRD}.state"]["travel_circles"][-1]["cast_day"] == 0
        assert hero["spell_materials"][0]["quantity"] == 2
    with AuthorityOwner.open(db, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(cast).status is ActionStatus.COMMITTED
        assert owner.snapshot("casting") == saved
        # The actual portal brings the learner within reach of a new destination sigil.
        move = replace(
            base,
            action_id="approach",
            idempotency_key="approach",
            expected_revision=saved.revision,
            tool_name="enter_srd_teleportation_circle",
            parameters={"actor_state": "hero", "caster_state": "hero"},
        )
        assert mechanics.execute_action(move).status is ActionStatus.COMMITTED
        arrived = owner.snapshot("casting")
        chunk = replace(
            base,
            action_id="chunk",
            idempotency_key="chunk",
            expected_revision=arrived.revision,
            tool_name="study_srd_circle_sigil",
            parameters={
                "actor_state": "hero",
                "circle_subject": "study-circle",
                "elapsed_rounds": 4,
                "uninterrupted": True,
            },
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(chunk)
        assert owner.snapshot("casting") == arrived
        study = replace(
            chunk,
            action_id="study",
            idempotency_key="study",
            parameters={"actor_state": "hero", "circle_subject": "study-circle"},
        )
        receipt = mechanics.execute_action(study)
        assert receipt.status is ActionStatus.COMMITTED
        completed = owner.snapshot("casting")
        assert completed.state[f"{SRD}.state"]["lasting_tick"] == 40
        assert completed.state[f"{SRD}.entities"]["hero"]["circle_study"] is None
        assert completed.state[f"{SRD}.entities"]["hero"]["known_circle_sigils"] == [
            "amber",
            "blue",
            "copper",
        ]
        assert mechanics.execute_action(study) == receipt and owner.snapshot("casting") == completed
