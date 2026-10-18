"""Saved trigger owners derive fictional-event consequences from source and admitted scene facts."""

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_triggers import CHOICES, PROFILES, SOURCE, _schema, _tools, validate_profiles
from scripts.srd_full.srd_timed_actor_defenses import _call, _when

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _free

ROOT = Path(__file__).resolve().parents[2]
PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-triggers/input-1"))


def _bind_paid_cast_for_test(runtime):
    """Use the real extended cast, component and slot owners before the independent leaf."""
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = runtime["tools"][name]
        existing = {p["name"] for p in tool["parameters"]}
        tool["parameters"] += [deepcopy(p) for p in CHOICES if p["name"] not in existing]
        if "apply_srd_trigger_spell" not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    "_spell_cast_failed != true and _spell_definition.family == 'trigger_spell'",
                    [
                        _call(
                            "apply_srd_trigger_spell",
                            {
                                **{p["name"]: "{" + p["name"] + "}" for p in CHOICES},
                                "caster_state": "{_caster_state_id}",
                                "spell": "{spell}",
                                "slot_level": "{_slot_level}",
                                "save_dc": "{_casting_profile.save_dc}",
                            },
                            ["_trigger_id", "_spell_summary_rows", "_spell_detail_rows"],
                        ),
                    ],
                )
            )
    # The source is newly admitted in this fragment; the actual owners otherwise stay intact.
    for tool in runtime["tools"].values():
        for parameter in tool.get("parameters", []):
            if parameter["name"] == "spell" and parameter["type"] == "enum":
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | PROFILES.keys())


@lru_cache
def _runtime():
    path = ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    runtime = yaml.load(path.read_bytes(), Loader=yaml.CSafeLoader)
    if "apply_srd_trigger_spell" not in runtime["tools"]:
        payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
        assert hashlib.sha256(payload).hexdigest() == (
            "cb949c4d5824c52e69f1f7d1bb27a5734b34b98827a65631947072db9c903d95"
        )
        runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
        runtime["definitions"]["spells"].update(deepcopy(PROFILES))
        runtime["definition_row_schemas"]["definitions.spells"]["variants"]["trigger_spell"] = (
            _schema()
        )
        runtime["tools"].update(_tools(runtime))
        _bind_paid_cast_for_test(runtime)
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )


def _case(spell, **choices):
    state, data, params = _information_case(spell)
    _free(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["entities"][2]["position"] = [5, 0, 0]
    scene["entities"][2]["metadata"].update(
        closable=True, carried_by="", worn_by="", lighting="bright"
    )
    scene["entities"].append(
        _entity("other", [5, 5, 0], participant_ref="other", creature_type="humanoid")
    )
    for entry in scene["entities"]:
        entry["footprint"] = {"kind": "box", "half_extents": [0.1, 0.1]}
    entities = state[f"{SRD}.entities"]
    entities["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    entities["hero"]["skill_modifiers"] = {"perception": 30}
    for identity in ("hero", "guard", "other"):
        entities[identity].update(
            creature_kind="character" if identity == "hero" else "monster",
            hp=1000,
            max_hp=1000,
            temp_hp=0,
        )
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", "min": [-10000, -10000, -10000], "max": [10000, 10000, 10000]},
            "metadata": {"lighting": "bright", "plane": "material"},
        }
    ]
    params.update(trigger_position=[5, 0, 0])
    if spell == "magic_mouth":
        params.update(
            trigger_anchor="orb",
            trigger_anchor_kind="object",
            trigger_events=["visual"],
            mouth_words=["Welcome", "traveler"],
            mouth_delivery_seconds=6,
        )
    if spell in ("glyph_of_warding", "symbol"):
        params.update(trigger_anchor="orb", trigger_anchor_kind="object", trigger_events=["open"])
        scene["entities"][1]["position"] = [6, 0, 0]
    params.update(choices)
    if spell == "symbol":
        for creature in scene["entities"]:
            if creature["metadata"].get("participant_ref"):
                creature["metadata"].setdefault("cover_from", {})[params["trigger_anchor"]] = "none"
    return state, data, params


def _cast(spell, **choices):
    state, data, params = _case(spell, **choices)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    rounds = 600 if spell == "glyph_of_warding" else 10
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=rounds,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    result = _run("cast_spell", state, participant_data=data, **params)
    return state, data, params, result.result["context"]["_trigger_id"]


def _source(state):
    return next(
        e for e in state[f"{SRD}.state"]["turn_effects"] if e.get("kind") == "spell_trigger"
    )


def _entry(state, identity):
    return next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == identity
    )


def _event(state, identity=1, actor="guard", event="enter", **values):
    return _run(
        "resolve_srd_spell_trigger",
        state,
        trigger_id=identity,
        event_actor=actor,
        event_kind=event,
        fictional_match=True,
        **values,
    ).result["context"]


def _heard(state, identity=1):
    return _run(
        "check_srd_alarm_listener", state, trigger_id=identity, listener_state="guard"
    ).result["context"]["_trigger_alarm_heard"]


def test_exact_source_profiles_and_full_glyph_continuation_are_pinned():
    corpus = (PARENT / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_bytes()
    assert hashlib.sha256(corpus).hexdigest() == (
        "b45fa14e10abc9a65083e3be8285fd747e67c468c49e5567fc5c16faeda3a0d0"
    )
    pages = {p["page"]: p["text"] for p in json.loads(corpus)["page_records"]}
    for _spell, (page, start, end) in SOURCE.items():
        numbers = page if isinstance(page, list) else [page]
        text = "\n".join(pages[n] for n in numbers)
        assert start in text and end in text
    validate_profiles(PROFILES)
    bad = deepcopy(PROFILES)
    bad["alarm"]["effect_rounds"] = 4799
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles(bad)


@pytest.mark.parametrize(
    "spell,slot,consumed",
    [("alarm", 1, False), ("magic_mouth", 2, True), ("glyph_of_warding", 3, True)],
)
def test_actual_long_cast_pays_source_slot_and_material_once_and_reopens(spell, slot, consumed):
    state, _, _, identity = _cast(spell)
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == (
        2 if consumed else 3
    )
    assert _source(state)["started_tick"] == (1200 if spell == "glyph_of_warding" else 20)
    assert _source(json.loads(json.dumps(state)))["instance"] == identity


def test_alarm_actual_cube_exemption_mental_mile_and_sleep_then_expiry():
    state, _, _, identity = _cast("alarm", trigger_exempt_creatures=["hero"])
    assert _event(state, identity, actor="hero")["_trigger_fired"] is False
    _entry(state, "guard")["position"] = [15.11, 0, 0]
    assert _event(state, identity)["_trigger_fired"] is False
    _entry(state, "guard")["position"] = [15.1, 0, 0]
    _entry(state, "hero")["position"] = [5285, 0, 0]
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(rest_added_unconscious=True, conditions=["unconscious"])
    assert _event(state, identity)["_trigger_alert_delivered"] is True
    assert state[f"{SRD}.entities"]["hero"]["conditions"] == []
    _entry(state, "hero")["position"] = [5285.01, 0, 0]
    assert _event(state, identity)["_trigger_alert_delivered"] is False
    state = json.loads(json.dumps(state))
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    assert not any(e.get("kind") == "spell_trigger" for e in state[f"{SRD}.state"]["turn_effects"])


def test_audible_alarm_persists_ten_seconds_actual_range_and_deafness():
    state, _, _, identity = _cast("alarm", alarm_mode="audible")
    context = _event(state, identity)
    assert context["_trigger_sound_ends_seconds"] == 70
    state = json.loads(json.dumps(state))
    _entry(state, "guard")["position"] = [65, 0, 0]
    assert _heard(state, identity) is True
    _entry(state, "guard")["position"] = [65.01, 0, 0]
    assert _heard(state, identity) is False
    _entry(state, "guard")["position"] = [65, 0, 0]
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["deafened"]
    assert _heard(state, identity) is False
    state[f"{SRD}.entities"]["guard"]["conditions"] = []
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _heard(state, identity) is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _heard(state, identity) is False


@pytest.mark.parametrize("profile", ["sleep", "eyebite_sleep"])
def test_alarm_wakes_actual_spell_sleep_preserving_independent_unconscious(profile):
    state, _, _, identity = _cast("alarm")
    installed = _run(
        "install_srd_timed_condition_source",
        state,
        target_state="hero",
        lasting_state="state",
        spell="sleep" if profile == "sleep" else "eyebite",
        condition="unconscious",
        save_dc=18,
        duration_rounds=100,
        repeat_save_timing="none",
        control_profile=profile,
    ).result["context"]["_timed_condition_source_id"]
    assert "unconscious" in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert _event(state, identity)["_trigger_alert_delivered"] is True
    assert state[f"{SRD}.state"]["timed_condition_sources"][installed]["installed"] is False
    assert "unconscious" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(conditions=["unconscious"], hp=0)
    assert _event(state, identity)["_trigger_alert_delivered"] is True
    assert state[f"{SRD}.entities"]["hero"]["conditions"] == ["unconscious"]


@pytest.mark.parametrize("repeat", [False, True])
def test_mouth_range_saved_message_delivery_and_optional_repetition(repeat):
    state, _, _, identity = _cast("magic_mouth", mouth_repeat=repeat)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [35.01, 0, 0]
    assert _event(state, identity, event="visual")["_trigger_fired"] is False
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [35, 0, 0]
    context = _event(state, identity, event="visual")
    assert context["_trigger_result"] == "Welcome traveler"
    assert context["_trigger_delivery_ends_seconds"] == 66
    state = json.loads(json.dumps(state))
    assert _event(state, identity, event="visual")["_trigger_fired"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    if repeat:
        assert _event(state, identity, event="visual")["_trigger_fired"] is True
    else:
        assert not any(
            row.get("kind") == "spell_trigger" and row.get("instance") == identity
            for row in state[f"{SRD}.state"]["turn_effects"]
        )
        with pytest.raises(RulesetPreconditionRejected, match="No saved"):
            _event(state, identity, event="visual")


def test_mouth_actual_holder_visibility_and_word_count_rejections_are_atomic():
    state, data, params = _case("magic_mouth")
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    for choice, error, message in [
        ({"mouth_words": ["word"] * 26}, PackageRuntimeError, None),
        ({"mouth_words": ["two words"]}, RulesetPreconditionRejected, "whitespace"),
    ]:
        before = deepcopy(state)
        with pytest.raises(error, match=message):
            _run("cast_spell", state, participant_data=data, **(params | choice))
        assert state == before
    orb["metadata"]["carried_by"] = "guard"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="another creature"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize("kind", ["surface", "object"])
def test_glyph_exact_movement_boundary_retires_even_when_returned(kind):
    state, _, _, identity = _cast("glyph_of_warding", trigger_anchor_kind=kind)
    _entry(state, "orb")["position"] = [15, 0, 0]
    _run("refresh_srd_spell_triggers", state)
    assert _source(state)["instance"] == identity
    _entry(state, "orb")["position"] = [15.01, 0, 0]
    _run("refresh_srd_spell_triggers", state)
    _entry(state, "orb")["position"] = [5, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="No saved"):
        _event(state, identity, event="open")


def test_glyph_notice_uses_actual_perception_original_dc_and_current_sight():
    state, _, _, identity = _cast("glyph_of_warding")
    source_dc = _source(state)["save_dc"]
    checked = _run(
        "notice_srd_spell_glyph", state, trigger_id=identity, observer_state="hero"
    ).result["context"]
    assert len(checked["_d20_test"]["results"]) == 1
    assert checked["_adjusted_total"] >= source_dc
    assert "success" in checked["_outcome"].lower()
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="cannot inspect"):
        _run("notice_srd_spell_glyph", state, trigger_id=identity, observer_state="hero")
    assert state == before
    state[f"{SRD}.entities"]["hero"]["conditions"] = []
    del state[f"{SRD}.entities"]["hero"]["skill_modifiers"]
    with pytest.raises(RulesetPreconditionRejected, match="actual saved"):
        _run("notice_srd_spell_glyph", state, trigger_id=identity, observer_state="hero")


@pytest.mark.parametrize("slot,damage_type", [(3, "fire"), (5, "cold")])
def test_explosive_glyph_actual_saved_dc_shared_roll_upcast_half_and_single_release(
    slot, damage_type
):
    state, data, params = _case("glyph_of_warding", glyph_damage_type=damage_type)
    params["slot_resource"] = f"spell_slot_{slot}"
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=600,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    result = _run("cast_spell", state, participant_data=data, **params)
    identity = result.result["context"]["_trigger_id"]
    source_dc = _source(state)["save_dc"]
    state[f"{SRD}.entities"]["hero"]["is_alive"] = False
    state[f"{SRD}.entities"]["hero"]["spellcasting_profile"] = {"save_dc": 1}
    state = json.loads(json.dumps(state))
    context = _event(state, identity, event="open")
    assert len(context["_trigger_damage_roll"]["results"]) == slot + 2
    total = context["_trigger_damage_total"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - total // 2
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000 - total
    assert f"DC {source_dc}" in str(context["_trigger_damage_details"])
    assert not state[f"{SRD}.state"]["has_spell_triggers"]
    with pytest.raises(RulesetPreconditionRejected, match="No saved"):
        _event(state, identity, event="open")


@lru_cache
def _test_archive():
    """Admit the authored fragment through the real strict package boundary."""
    runtime, payload = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for row in record["tools"]:
        if row["name"] in runtime["tools"]:
            row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    added = sorted(["resolve_srd_spell_trigger", "notice_srd_spell_glyph"])
    for name in added:
        if any(row["name"] == name for row in record["tools"]):
            continue
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
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": payload}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "independent-trigger-spells-owner",
            "kind": "ruleset_runtime",
            "family": "trigger_spell",
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
                "tools": added,
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["trigger-public-cast-release-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def test_actual_public_paid_glyph_release_saved_cards_reopen_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    database = tmp_path / "triggers.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "glyph_of_warding")
        begun, _ = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert begun.status is ActionStatus.COMMITTED
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED
        cast, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        identity = cast.result["context"]["_trigger_id"]
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "resolve_srd_spell_trigger",
                trigger_id=identity,
                event_actor="guard",
                event_kind="open",
                fictional_match=True,
                damage=999,
                save_roll=1,
            )
        saved_armed = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved_armed
        fired, command = _act(
            owner,
            "resolve_srd_spell_trigger",
            trigger_id=identity,
            event_actor="guard",
            event_kind="open",
            fictional_match=True,
        )
        assert fired.status is ActionStatus.COMMITTED, fired.result
        assert len(fired.result["context"]["_trigger_damage_roll"]["results"]) == 5
        assert "Saving throw" in str(fired.result["result_card"])
        saved_spent = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved_spent

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A saved trigger retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert owner.execute_action(command) == fired
        assert owner.snapshot("casting") == saved_spent
