"""GM hotfix regressions through the real owner, saved state and provider loop."""

import io
import json
import re
import shutil
from importlib.resources import files
from pathlib import Path

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.agent._provider_session import (
    ProviderSessionAdapter,
    _derive_mechanics_parameters,
    _normalize_state_references,
)
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    SessionRunRequest,
    _mechanics_rejection_feedback,
)
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.reusable_participants import validate_reusable_participant_draft
from sylvia_gm.domain.authority import (
    PackageRole,
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._base import ProviderStreamChunk
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef
from tests.qa_attached_request import request_attached
from tests.test_alpha_f4_generic_sessions import _campaign
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.test_fc_campaign_journey import _start_campaign
from tests.test_srd_action_budget import _state

SRD = "sylvia.srd-lite.bg3"


@pytest.fixture(scope="module")
def recipient_preflight_contract():
    runtime = yaml.load(
        (
            Path(__file__).resolve().parents[1] / "rulesets/srd-lite/runtime/ruleset.yaml"
        ).read_bytes(),
        Loader=yaml.CSafeLoader,
    )
    return runtime


@pytest.mark.parametrize("tool_name", ("begin_srd_spell_action", "cast_spell_outside_combat"))
@pytest.mark.parametrize("in_combat", (False, True))
@pytest.mark.parametrize(
    "spell,target,targets,accepted_modes",
    (
        ("sunburst", None, None, (True, True)),
        ("fire_storm", None, [], (True, True)),
        ("bless", None, None, (False, False)),
        ("bless", "hero", None, (False, False)),
        ("bless", None, [], (False, False)),
        ("bless", None, ["hero"], (True, True)),
        ("hold_person", "hero", None, (False, False)),
        ("longstrider", "hero", None, (False, False)),
        ("fire_shield", "hero", None, (True, True)),
        ("beacon_of_hope", "hero", None, (True, True)),
        ("mass_cure_wounds", None, None, (False, False)),
        ("fireball", None, None, (False, True)),
        ("fireball", None, [], (True, True)),
        ("fireball", None, ["hero"], (True, True)),
        ("shatter", None, [], (True, True)),
        ("ice_storm", None, None, (True, True)),
        ("burning_hands", None, None, (False, True)),
        ("flame_strike", None, None, (False, True)),
        ("acid_splash", None, None, (False, True)),
        ("circle_of_death", None, None, (False, True)),
        ("lightning_bolt", None, None, (False, True)),
        ("cure_wounds", None, ["hero"], (False, False)),
        ("magic_missile", None, ["hero"], (False, False)),
        ("cure_wounds", "hero", None, (True, True)),
    ),
)
def test_recipient_preflight_preserves_engine_and_caller_ownership(
    recipient_preflight_contract, tool_name, in_combat, spell, target, targets, accepted_modes
):
    runtime = recipient_preflight_contract
    checks = [
        check
        for check in runtime["tools"][tool_name]["pre_conditions"]
        if check["error"].startswith(("Missing input 'target'.", "Missing input 'targets'."))
    ]
    assert checks
    gate = ToolDef(
        name="recipient-preflight",
        pre_conditions=checks,
        effects=[{"set": {"field": "_admitted", "value": True}}],
    )
    result = execute_tool(
        gate.name,
        {
            "spell": spell,
            "target": target,
            "targets": targets,
            "combat": {"encounter_active": in_combat},
            "object_target_id": "",
            "disintegrate_source_kind": "creature",
        },
        {gate.name: gate},
        None,
        {},
        runtime["definitions"],
    )
    accepted = accepted_modes[in_combat]
    assert result.success is accepted
    if accepted:
        assert result.context["_admitted"] is True
    else:
        assert len(result.failure_reasons) == 1
        assert result.failure_reasons[0].startswith("Missing input '")
        assert "_admitted" not in result.context


def test_recipient_ownership_follows_bindings_with_unrelated_spell_names():
    from scripts.srd_lite_recipient_contract import recipient_contracts, recipient_requirements

    runtime = {
        "definitions": {
            "srd_lite_implemented": ["novel_area", "novel_chosen", "novel_self"],
            "srd_lite_aliases": {"novel_alias": "novel_chosen"},
            "spells": {
                "novel_area": {"family": "area", "range_kind": "distance"},
                "novel_chosen": {"family": "selection", "range_kind": "distance"},
                "novel_self": {"family": "selection", "range_kind": "self"},
            },
        },
        "tools": {
            "cast_spell": {
                "parameters": [{"name": name} for name in ("spell", "target", "targets")],
                "effects": [
                    {"set": {"field": "_area_values", "value": "targets"}},
                    {
                        "condition": (
                            "combat.encounter_active == true and _spell_definition.family == 'area'"
                        ),
                        "on_true": [
                            {"set": {"field": "_area_values", "value": ["'scene-recipient'"]}}
                        ],
                    },
                    {"set": {"field": "_chosen_values", "value": []}},
                    {
                        "condition": "targets != null",
                        "on_true": [{"set": {"field": "_chosen_values", "value": "targets"}}],
                    },
                    {
                        "condition": "target != null",
                        "on_true": [
                            {"list_push": {"list": "_chosen_values", "value": "_target_id"}}
                        ],
                    },
                    {
                        "condition": "_spell_definition.range_kind == 'self'",
                        "on_true": [
                            {"set": {"field": "_chosen_values", "value": ["_caster_state_id"]}}
                        ],
                    },
                    {
                        "condition": "_spell_definition.family == 'selection'",
                        "on_true": [
                            {"call": "cast_selection", "params": {"targets": "{_chosen_values}"}}
                        ],
                    },
                    {
                        "condition": "_spell_definition.family == 'area'",
                        "on_true": [{"call": "cast_area", "params": {"targets": "{_area_values}"}}],
                    },
                ],
            },
            "cast_selection": {
                "parameters": [{"name": "targets", "required": True}],
                "effects": [
                    {"require": {"condition": "len(targets) > 0", "error": "Choose recipients"}}
                ],
            },
            "cast_area": {"parameters": [{"name": "targets", "required": True}], "effects": []},
        },
    }
    assert recipient_requirements(runtime, "novel_area", False) == [
        ("targets", ("targets",), False, "true")
    ]
    assert recipient_requirements(runtime, "novel_area", True) == []
    for in_combat in (False, True):
        assert recipient_requirements(runtime, "novel_chosen", in_combat) == [
            ("targets", ("target", "targets"), True, "true")
        ]
        assert recipient_requirements(runtime, "novel_self", in_combat) == []
    groups = recipient_contracts(runtime).values()
    assert all(
        "novel_alias" in group["spells"] for group in groups if "novel_chosen" in group["spells"]
    )


@pytest.mark.parametrize("normalize_before_call", (False, True))
def test_recipient_derivation_uses_call_time_and_family_normalization(normalize_before_call):
    from scripts.srd_lite_recipient_contract import recipient_requirements

    bind = {"set": {"field": "_bound", "value": "targets"}}
    engine = {"set": {"field": "_bound", "value": ["'engine-owned'"]}}
    call = {"call": "cast_new_family", "params": {"targets": "{_bound}"}}
    runtime = {
        "definitions": {"spells": {"new_spell": {"family": "new_family"}}},
        "tools": {
            "cast_spell": {
                "parameters": [{"name": "targets"}],
                "effects": [bind, engine, call] if normalize_before_call else [bind, call, engine],
            },
            "cast_new_family": {
                "parameters": [{"name": "targets", "required": True}],
                "effects": [
                    {
                        "condition": "len(targets) == 0",
                        "on_true": [
                            {"set": {"field": "targets", "value": ["'derived-recipient'"]}}
                        ],
                    },
                    {"require": {"condition": "len(targets) > 0", "error": "Recipients"}},
                ],
            },
        },
    }
    expected = [] if normalize_before_call else [("targets", ("targets",), False, "true")]
    assert recipient_requirements(runtime, "new_spell", False) == expected


def test_recipient_presence_invariant_covers_every_implemented_spell_and_root(
    recipient_preflight_contract,
):
    from scripts.srd_lite_gm_feedback import CAST_ROOTS

    runtime = recipient_preflight_contract
    assert len(runtime["definitions"]["srd_lite_implemented"]) == 117
    for root in CAST_ROOTS:
        checks = [
            p
            for p in runtime["tools"][root]["pre_conditions"]
            if p["error"].startswith(("Missing input 'target'.", "Missing input 'targets'."))
        ]
        gate = ToolDef(name="recipient-presence", pre_conditions=checks, effects=[])
        for spell in runtime["definitions"]["srd_lite_implemented"]:
            for combat in (False, True):
                result = execute_tool(
                    gate.name,
                    {
                        "spell": spell,
                        "target": "primary",
                        "targets": ["primary"],
                        "combat": {"encounter_active": combat},
                        "object_target_id": "",
                        "disintegrate_source_kind": "creature",
                    },
                    {gate.name: gate},
                    None,
                    {},
                    runtime["definitions"],
                )
                assert result.success, (root, spell, combat, result.failure_reasons)


@pytest.mark.parametrize(
    "spell,selector,value,accepted",
    (
        ("fire_bolt", "object_target_id", "practice-door", True),
        ("fire_bolt", "object_target_id", "", False),
        ("disintegrate", "disintegrate_source_kind", "wall_of_force", True),
        ("disintegrate", "disintegrate_source_kind", "creature", False),
    ),
)
def test_recipient_check_preserves_noncreature_routes(
    recipient_preflight_contract, spell, selector, value, accepted
):
    runtime = recipient_preflight_contract
    checks = [
        p
        for p in runtime["tools"]["cast_spell_outside_combat"]["pre_conditions"]
        if p["error"].startswith("Missing input 'target'.")
    ]
    gate = ToolDef(name="recipient-route", pre_conditions=checks, effects=[])
    parameters = {
        "spell": spell,
        "target": None,
        "targets": None,
        "combat": {"encounter_active": False},
        "object_target_id": "",
        "disintegrate_source_kind": "creature",
        selector: value,
    }
    result = execute_tool(
        gate.name, parameters, {gate.name: gate}, None, {}, runtime["definitions"]
    )
    assert result.success is accepted


@pytest.mark.parametrize(
    "spell,caster", (("bless", "cleric"), ("hold_person", "wizard"), ("longstrider", "wizard"))
)
@pytest.mark.parametrize("in_combat", (False, True))
def test_staged_caller_selected_recipients_still_refuse_before_spending(
    tmp_path, monkeypatch, spell, caster, in_combat
):
    archive = tmp_path / "current-lite.sylvia-gm-package"
    archive.write_bytes(bundled_srd_archive())
    monkeypatch.setenv("SRD_LITE_CANDIDATE", str(archive))
    from tests import test_srd_lite_foundation as support

    monkeypatch.setattr(support, "ARCHIVE", archive)
    case = support.Case(tmp_path / "manual-targets.sqlite3", spell, casting_source=caster)
    try:
        if in_combat:
            case.start()
        samples = []
        randint = dice._rng.randint

        def observed_sample(*args, **kwargs):
            samples.append((args, kwargs))
            return randint(*args, **kwargs)

        # An action reseeds normally; refusal must not sample that generator.
        monkeypatch.setattr(dice._rng, "randint", observed_sample)
        root = "begin_srd_spell_action" if in_combat else "cast_spell_outside_combat"
        case.reject(root, case.parameters, match="Missing input 'targets'")
        assert samples == []
        assert case.entities()["hero"]["resources"][case.parameters["slot_resource"]] == 3
        assert case.snapshot().state[case.record["id"] + ".combat"]["action_available"] is in_combat
    finally:
        case.close()


@pytest.fixture(scope="module")
def recipient_archive_pair(tmp_path_factory):
    current = tmp_path_factory.mktemp("recipient-archive") / "current.sylvia-gm-package"
    current.write_bytes(bundled_srd_archive())
    baseline = Path(__file__).resolve().parent / "fixtures/alpha4-lite.sylvia-gm-package"
    return baseline, current


@pytest.mark.parametrize(
    "spell,in_combat,case_options,choices,drop_target",
    (
        ("fire_shield", False, {}, {"target": "hero"}, False),
        ("beacon_of_hope", False, {"casting_source": "cleric"}, {}, False),
        ("ice_storm", False, {}, {"environment_origin": [5, 0, 0]}, False),
        ("ice_storm", True, {}, {"environment_origin": [5, 0, 0]}, False),
        (
            "burning_hands",
            True,
            {},
            {"environment_origin": [0, 0, 0], "instant_area_direction": [1, 0, 0]},
            False,
        ),
        (
            "flame_strike",
            True,
            {"casting_source": "cleric"},
            {"environment_origin": [5, 0, 0]},
            False,
        ),
        ("fireball", False, {}, {"targets": [], "origin_distance": 25}, True),
        ("shatter", False, {}, {"targets": [], "origin_distance": 25}, True),
    ),
)
def test_recipient_contract_keeps_baseline_accepted_casts(
    tmp_path,
    monkeypatch,
    recipient_archive_pair,
    spell,
    in_combat,
    case_options,
    choices,
    drop_target,
):
    """Each baseline-accepted call crosses its real casting root in both packages."""
    baseline, current = recipient_archive_pair
    monkeypatch.setenv("SRD_LITE_CANDIDATE", str(current))
    from tests import test_srd_lite_foundation as support

    for label, archive in (("baseline", baseline), ("hotfix", current)):
        monkeypatch.setattr(support, "ARCHIVE", archive)
        case = support.Case(tmp_path / (label + ".sqlite3"), spell, **case_options)
        try:
            parameters = case.parameters | choices
            if drop_target:
                parameters.pop("target")
            if in_combat:
                case.start()
            root = "begin_srd_spell_action" if in_combat else "cast_spell_outside_combat"
            result = case.execute(case.command(root, parameters))
            assert result.status.value == "committed"
            assert case.entities()["hero"]["resources"][case.parameters["slot_resource"]] == (
                3 if in_combat else 2
            )
            if in_combat:
                assert not case.snapshot().state[case.record["id"] + ".combat"]["action_available"]
                assert result.result["context"]["_pending_action_id"]
            elif choices.get("targets") == []:
                assert case.entities()["guard"]["hp"] == 1000
                assert case.entities()["other"]["hp"] == 1000
                assert case.entities()["extra"]["hp"] == 1000
        finally:
            case.close()


def test_all_top_level_refusals_are_reported_before_effects():
    tool = ToolDef(
        name="blocked",
        pre_conditions=[
            {"condition": "false", "error": "First missing input."},
            {"condition": "false", "error": "Second missing input."},
        ],
        effects=[{"set": {"field": "_written", "value": True}}],
    )
    result = execute_tool("blocked", {}, {"blocked": tool}, None, {}, {})
    assert not result.success
    assert result.failure_reasons == ("First missing input.", "Second missing input.")
    assert "_written" not in result.context


def test_missing_derivation_source_names_the_input_and_uses_actual_feedback_fields():
    with pytest.raises(MechanicsRejected) as refused:
        _derive_mechanics_parameters(
            {},
            {"actor": {"kind": "copy", "source": "actor_state"}},
            (),
        )
    feedback = _mechanics_rejection_feedback(refused.value)
    assert feedback["code"] == "mechanics_derivation_source_missing"
    assert "actor_state" in feedback["reason"] and "actor" in feedback["reason"]
    assert feedback["validation"]["field_paths"] == ["actor_state"]
    assert "validation.issues" not in feedback["instruction"]


@pytest.mark.parametrize(
    "display,canonical",
    [
        ("Cure Wounds", "cure_wounds"),
        ("CURE-WOUNDS", "cure_wounds"),
        (" cure_wounds ", "cure_wounds"),
        ("Hunter's Mark", "hunters_mark"),
        ("Hunter\u2019s Mark", "hunters_mark"),
    ],
)
def test_spell_name_forms_and_exact_participant_fill_preserve_authorization(display, canonical):
    schema = {
        "properties": {
            "spell": {"type": "string", "enum": [canonical]},
            "caster": {"type": "string", "x-sylvia-gm-reference": "participant", "enum": ["hero"]},
            "caster_state": {"type": "string"},
        }
    }
    state = {"game.entities": {"hero": {}, "monster": {}}}
    assert _normalize_state_references(
        {"caster_state": "hero", "spell": display}, schema, state
    ) == {
        "caster_state": "hero",
        "caster": "hero",
        "spell": canonical,
    }
    assert "caster" not in _normalize_state_references({"caster_state": "monster"}, schema, state)
    assert _normalize_state_references(
        {"caster": "hero", "caster_state": "monster"}, schema, state
    ) == {
        "caster": "hero",
        "caster_state": "monster",
    }


def test_permanent_refusal_detection_requires_first_unconditional_literal_false():
    refusal = {"require": {"condition": "false", "error": "Unavailable"}}
    runtime = RulesetRuntime(
        None,
        {
            "tools": {
                "stub": {"effects": [refusal]},
                "stub_with_tail": {"effects": [refusal, {"set": {"field": "x", "value": 1}}]},
                "conditional": {"effects": [{"require": {"condition": "x == false"}}]},
                "later": {"effects": [{"set": {"field": "x", "value": 1}}, refusal]},
                "nested": {"effects": [{"if": {"condition": "x", "on_true": [refusal]}}]},
            }
        },
    )
    assert runtime.permanently_refused_tool_names == frozenset({"stub", "stub_with_tail"})


def create_cleric_save(database, archive):
    """Create an admitted actual starter Cleric against the selected retained package."""
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        candidate = owner.install_package_archive(io.BytesIO(archive), install_id="starter")
        primary = next(p for p in candidate.package_lock.entries if p.role is PackageRole.PRIMARY)
        declaration = next(
            d
            for d in owner.exact_participant_declarations(
                primary.package_id,
                primary.version,
                primary.sha256,
            )
            if d.kind == "player_character"
        )
        starter = next(
            row
            for row in json.loads(
                files("sylvia_gm.campaign").joinpath("srd-starters.json").read_bytes()
            )
            if row["id"] == "cleric"
        )
        _, data, entity, _ = validate_reusable_participant_draft(
            role="player_character",
            summary=starter["summary"],
            authored_profile=starter["authored_profile"],
            participant=starter["participant"],
            entity_state=starter["entity_state"],
            declaration=declaration,
        )
        assert (entity["hp"], entity["armor_class"], entity["resources"]["spell_slot_1"]) == (
            9,
            17,
            2,
        )
        entity["hp"] = 4
        state = _state()
        state[f"{SRD}.state"] = {
            "advancement": {
                "method": "fixed",
                "starting_level": 1,
                "maximum_level": 1,
                "recipients": "whole_party",
                "timing": "No advancement awards.",
                "allocation": "No advancement awards.",
            }
        }
        state[f"{SRD}.entities"]["hero"] = entity
        state[f"{SRD}.entities"]["guard"] = {
            "name": "Guard",
            "creature_kind": "monster",
            "hp": 7,
            "max_hp": 9,
            "armor_class": 12,
            "is_alive": True,
            "size_rank": 1,
            "conditions": [],
            "prepared_spells": [],
            "resources": {},
            "resource_maxima": {},
        }
        state[f"{SRD}.combat"].update(
            encounter_active=False,
            turn_active=False,
            active_actor=None,
            phase="exploration",
            action_available=False,
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="casting",
                display_name="Starter Cleric",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="casting-turn",
                participants=(
                    Participant(
                        participant_id="hero",
                        kind=declaration.kind,
                        package_id=primary.package_id,
                        package_version=primary.version,
                        package_sha256=primary.sha256,
                        schema_id=declaration.schema_id,
                        schema_sha256=declaration.schema_sha256,
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
        owner.create_agent_session(
            campaign_id="casting",
            session_id="gm",
            role=WriterRole.DM,
            provider_id="fixture",
            model_id="fixture",
        )
    return database


@pytest.fixture(scope="module")
def lite_save(tmp_path_factory):
    return create_cleric_save(
        tmp_path_factory.mktemp("hotfix-lite") / "starter.sqlite3",
        bundled_srd_archive(),
    )


def test_actual_gm_catalog_excludes_permanent_refusals_and_preserves_lock(lite_save, tmp_path):
    database = tmp_path / "catalog.sqlite3"
    shutil.copyfile(lite_save, database)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.current_package_lock("casting")
        owner.append_agent_message(
            session_id="gm",
            message_id="golden-basis",
            kind=SessionMessageKind.USER,
            content={"text": "Remember the established bell."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="golden-fact",
            turn_id="casting-turn",
            basis_message_ids=("golden-basis",),
            updates=(
                {
                    "operation": "record_memory",
                    "fact_id": "golden-bell",
                    "category": "continuity",
                    "text": "The north bell rings in pairs.",
                    "visibility": "player_known",
                    "known_by": ["hero"],
                },
            ),
        )
        request = play(owner, CaptureGM(), campaign="casting", turn="casting-turn")
        assert owner.current_package_lock("casting") == before
        for entry in before.package_lock.entries:
            if entry.role is PackageRole.PRIMARY:
                from sylvia_gm.application.package_runtime import _parse_runtime

                runtime = _parse_runtime(
                    package_id=entry.package_id,
                    runtime_bytes=owner._store.package_file_bytes(
                        entry.package_id,
                        entry.version,
                        entry.sha256,
                        "runtime/ruleset.yaml",
                        maximum_bytes=30_000_000,
                    ),
                )
    tools = request["tools"]
    names = [tool["function"]["name"] for tool in tools]
    assert not any(
        re.sub(r"^tool_[0-9a-f]{12}_", "", name) in runtime.permanently_refused_tool_names
        for name in names
    )
    fresh = next(
        m
        for m in request["messages"]
        if str(m.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    )
    assert "golden-bell" in json.dumps(
        json.loads(fresh["content"].split("\n", 1)[1])["PLAYER_KNOWN"]
    )
    # Frozen Cleric request was 413,576 bytes after stub removal. Allow the measured
    # casting descriptions and enums, while catching a return to 549,640 stub bytes.
    assert len(json.dumps(tools).encode("utf-8")) < 450_000
    assert not any(name.endswith("_select_srd_clone_body") for name in names)
    assert any(name.endswith("_cast_spell_outside_combat") for name in names)
    assert any(name.endswith("_read_campaign_reference") for name in names)
    assert len(tools) == 154
    casting = next(
        t["function"] for t in tools if t["function"]["name"].endswith("_cast_spell_outside_combat")
    )
    assert set(casting["parameters"]["properties"]["spell"]["enum"]) == {
        "guidance",
        "sacred_flame",
        "light",
        "bless",
        "cure_wounds",
        "guiding_bolt",
        "healing_word",
        "spare_the_dying",
        "mending",
        "command",
    }


def test_retained_alpha4_catalog_filters_stubs_without_upgrading_the_saved_pin(tmp_path):
    archive = (Path(__file__).parent / "fixtures/alpha4-lite.sylvia-gm-package").read_bytes()
    database = create_cleric_save(tmp_path / "retained.sqlite3", archive)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.current_package_lock("casting")
        primary = next(p for p in before.package_lock.entries if p.role is PackageRole.PRIMARY)
        assert primary.version == "0.3.15"
        declarations = owner.package_record(primary.package_id, primary.version, primary.sha256)[
            "tools"
        ]
        assert any(tool["name"] == "select_srd_clone_body" for tool in declarations)
        request = play(owner, CaptureGM(), campaign="casting", turn="casting-turn")
        assert owner.current_package_lock("casting") == before
    names = [tool["function"]["name"] for tool in request["tools"]]
    assert not any(name.endswith("_select_srd_clone_body") for name in names)
    assert any(name.endswith("_cast_spell_outside_combat") for name in names)
    assert any(name.endswith("_read_campaign_reference") for name in names)
    assert len(names) == 154


class CaptureGM(LibraryProvider):
    """Capture assembled requests and settle using the advertised campaign-file tool."""

    def __init__(self):
        super().__init__()
        self.requests = []

    async def generate_stream(self, messages, tools=None, **kwargs):
        self.requests.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        name = next(
            t["function"]["name"]
            for t in tools
            if t["function"]["name"].endswith("_update_campaign_file")
        )
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"settle-{len(self.requests)}",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        {
                            "state_change": "unchanged",
                            "state_change_summary": "The saved scene remains current.",
                            "revised_player_facing_response": [
                                {"kind": "narration", "text": "You wait."}
                            ],
                        }
                    ),
                },
            },
            finish_reason="tool_calls",
        )


class HealingGM(CaptureGM):
    """Choose advertised inputs, then add only fields named in actual refusal feedback."""

    def __init__(self):
        super().__init__()
        self.arguments = None
        self.cast_calls = 0
        self.refusals = []
        self.results = []
        self.observe_state = None
        self.observations = []

    async def generate_stream(self, messages, tools=None, **kwargs):
        if self.observe_state is not None:
            self.observations.append(self.observe_state())
        result_message = next((m for m in reversed(messages) if m.get("role") == "tool"), None)
        result = json.loads(result_message["content"]) if result_message else None
        if result is not None:
            self.results.append(result)
        if result is not None and result.get("status") == "action_committed":
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk
            return
        self.requests.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        cast = next(
            t["function"]
            for t in tools
            if t["function"]["name"].endswith("_cast_spell_outside_combat")
        )
        schema = cast["parameters"]
        fresh = next(
            m
            for m in messages
            if str(m.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
        )
        state = json.loads(fresh["content"].split("\n", 1)[1])["campaign_state"][
            "ruleset_owned_values"
        ]
        actor = schema["properties"]["caster"]["enum"][0]
        entity = state[f"{SRD}.entities"][actor]
        if self.arguments is None:
            assert set(schema["required"]) == {"caster_state", "spell"}
            spell = next(
                value
                for value in schema["properties"]["spell"]["enum"]
                if value.replace("_", " ") == "cure wounds"
            )
            self.arguments = {"caster_state": actor, "spell": spell.replace("_", " ").title()}
        elif result is not None:
            self.refusals.append(result)
            feedback = json.dumps(result)
            missing = set(re.findall(r"Missing input '([^']+)'", feedback))
            assert "somatic_component_available" not in missing
            for name in missing:
                assert name in schema["properties"], (name, result)
                if name in {"caster", "caster_state", "target"}:
                    value = actor
                elif name == "slot_resource":
                    value = next(
                        k
                        for k, v in entity["resources"].items()
                        if k.startswith("spell_slot_") and v > 0
                    )
                elif schema["properties"][name].get("type") == "boolean":
                    value = True
                else:
                    raise AssertionError(f"No scene fact supplies {name}")
                self.arguments[name] = value
        self.cast_calls += 1
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"heal-{self.cast_calls}",
                "function": {"name": cast["name"], "arguments": json.dumps(self.arguments)},
            },
            finish_reason="tool_calls",
        )


class DefectGM(CaptureGM):
    """Exercise one advertised operation, then settle its actual result."""

    def __init__(self, operation, arguments):
        super().__init__()
        self.operation = operation
        self.arguments = arguments
        self.receipt = None
        self.sent = False

    async def generate_stream(self, messages, tools=None, **kwargs):
        if self.sent:
            result = next(m for m in reversed(messages) if m.get("role") == "tool")
            self.receipt = json.loads(result["content"])
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk
            return
        self.sent = True
        self.requests.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        name = next(
            t["function"]["name"]
            for t in tools
            if t["function"]["name"].endswith("_" + self.operation)
        )
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": "defect-control",
                "function": {"name": name, "arguments": json.dumps(self.arguments)},
            },
            finish_reason="tool_calls",
        )


@pytest.mark.parametrize(
    "operation,arguments,expected",
    [
        (
            "restore_srd_resource",
            {"target": "hero", "resource": "action_surge", "amount": 1},
            "rejected",
        ),
        (
            "record_srd_contingency_world_fact",
            {"caster_state": "hero", "fact_tag": "bell_rang"},
            "action_committed",
        ),
    ],
)
def test_named_ruleset_failures_use_safe_refusal_or_valid_noop(
    lite_save, tmp_path, operation, arguments, expected
):
    database = tmp_path / "named-defect.sqlite3"
    shutil.copyfile(lite_save, database)
    provider = DefectGM(operation, arguments)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.snapshot("casting")
        play(owner, provider, campaign="casting", turn="casting-turn")
        after = owner.snapshot("casting")
    assert provider.receipt["status"] == expected, provider.receipt
    if expected == "rejected":
        assert provider.receipt["code"] == "ruleset_precondition_failed"
        assert "recorded resource" in provider.receipt["reason"]
        assert after.state == before.state
        assert after.revision == before.revision
    assert after.state[f"{SRD}.entities"]["hero"]["hp"] == 4
    assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2


def test_advertised_healing_flow_corrects_once_and_commits_exact_effects(lite_save, tmp_path):
    database = tmp_path / "healing.sqlite3"
    shutil.copyfile(lite_save, database)
    provider = HealingGM()
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.snapshot("casting")
        provider.observe_state = lambda: (
            owner.snapshot("casting").revision,
            owner.snapshot("casting").state,
        )
        play(
            owner,
            provider,
            campaign="casting",
            turn="casting-turn",
            user_text="I cast Cure Wounds on myself.",
        )
        after = owner.snapshot("casting")
    assert provider.cast_calls == 2
    assert provider.observations[0] == provider.observations[1]
    assert len(provider.refusals) == 1
    refused = provider.refusals[0]
    assert refused["state_changed"] is False
    missing = set(re.findall(r"Missing input '([^']+)'", json.dumps(refused)))
    assert missing == {
        "target",
        "slot_resource",
        "verbal_component_available",
    }
    assert "somatic_component_available" not in provider.arguments
    assert "validation.issues" not in refused["instruction"]
    assert after.state[f"{SRD}.entities"]["hero"]["hp"] == 9
    assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 1
    assert after.state[f"{SRD}.entities"]["guard"] == before.state[f"{SRD}.entities"]["guard"]
    committed = [
        action for action in after.actions if action.tool_name == "cast_spell_outside_combat"
    ]
    assert len(committed) == 1
    assert committed[0].parameters["spell"] == "cure_wounds"
    assert committed[0].parameters["target"] == "hero"


def test_explicitly_unavailable_somatic_component_refuses_without_spending(lite_save, tmp_path):
    database = tmp_path / "somatic-refused.sqlite3"
    shutil.copyfile(lite_save, database)
    provider = DefectGM(
        "cast_spell_outside_combat",
        {
            "caster_state": "hero",
            "spell": "Cure Wounds",
            "target": "hero",
            "slot_resource": "spell_slot_1",
            "verbal_component_available": True,
            "somatic_component_available": False,
        },
    )
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.snapshot("casting")
        play(owner, provider, campaign="casting", turn="casting-turn")
        after = owner.snapshot("casting")
    assert provider.receipt["status"] == "rejected"
    assert provider.receipt["state_changed"] is False
    assert "somatic_component_available" in json.dumps(provider.receipt)
    assert after.state == before.state and after.revision == before.revision


class TouchHealingGM(HealingGM):
    """Replay the other-creature touch omission, obeying only the returned correction."""

    def __init__(self):
        super().__init__()
        self.arguments = {
            "caster_state": "hero",
            "spell": "Cure Wounds",
            "target": "guard",
            "slot_resource": "spell_slot_1",
            "verbal_component_available": True,
        }


def test_other_creature_touch_refusal_corrects_once_with_exact_effects(lite_save, tmp_path):
    database = tmp_path / "touch-correction.sqlite3"
    shutil.copyfile(lite_save, database)
    provider = TouchHealingGM()
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.snapshot("casting")
        provider.observe_state = lambda: (
            owner.snapshot("casting").revision,
            owner.snapshot("casting").state,
        )
        play(
            owner,
            provider,
            campaign="casting",
            turn="casting-turn",
            user_text="I cast Cure Wounds on the guard.",
        )
        after = owner.snapshot("casting")
    assert provider.cast_calls == 2
    assert provider.observations[0] == provider.observations[1]
    assert len(provider.refusals) == 1
    refused = provider.refusals[0]
    assert refused["state_changed"] is False
    assert set(re.findall(r"Missing input '([^']+)'", json.dumps(refused))) == {
        "caster_can_touch_target"
    }
    assert "true" in refused["reason"]
    assert provider.arguments["caster_can_touch_target"] is True
    cast = next(
        t["function"]
        for t in provider.requests[0]["tools"]
        if t["function"]["name"].endswith("_cast_spell_outside_combat")
    )
    assert "touch" in cast["parameters"]["properties"]["caster_can_touch_target"]["description"]
    assert after.state[f"{SRD}.entities"]["hero"]["hp"] == 4
    assert after.state[f"{SRD}.entities"]["guard"]["hp"] == 9
    assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 1
    committed = [a for a in after.actions if a.tool_name == "cast_spell_outside_combat"]
    assert len(committed) == 1
    assert committed[0].parameters["spell"] == "cure_wounds"
    assert committed[0].parameters["target"] == "guard"
    assert before.state[f"{SRD}.entities"]["guard"]["hp"] == 7


@pytest.mark.parametrize(
    "spell,scene,field",
    (
        ("cure_wounds", {"caster_can_touch_target": False}, "caster_can_touch_target"),
        (
            "healing_word",
            {"target_distance": 120, "caster_can_see_target": True},
            "target_distance",
        ),
        ("healing_word", {"caster_can_see_target": True}, "target_distance"),
    ),
)
def test_range_refusals_name_scene_inputs_and_preserve_state(
    lite_save, tmp_path, spell, scene, field
):
    database = tmp_path / "range-refused.sqlite3"
    shutil.copyfile(lite_save, database)
    provider = DefectGM(
        "cast_spell_outside_combat",
        {
            "caster_state": "hero",
            "spell": spell,
            "target": "guard",
            "slot_resource": "spell_slot_1",
            "verbal_component_available": True,
            **scene,
        },
    )
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.snapshot("casting")
        play(owner, provider, campaign="casting", turn="casting-turn")
        after = owner.snapshot("casting")
    assert provider.receipt["status"] == "rejected"
    assert provider.receipt["code"] == "ruleset_precondition_failed"
    assert provider.receipt["state_changed"] is False
    assert field in provider.receipt["reason"]
    assert after.state == before.state and after.revision == before.revision


def play(owner, provider, *, campaign="memory", session="gm", turn="turn", user_text="I wait."):
    adapter = ProviderSessionAdapter(owner, provider, owner_id="hotfix-owner")
    result = CampaignSessionLoop(owner).run(
        SessionRunRequest(session, turn, "capture", "capture-input", user_text),
        adapter.for_turn(turn),
    )
    assert result.interruption is None, result.interruption
    return provider.requests[0]


def test_saved_memory_reaches_actual_provider_after_reopen_without_history_answers(tmp_path):
    database = tmp_path / "memory.sqlite3"
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        _campaign(owner, campaign_id="memory", writer=WriterRole.DM, turn_id="turn")
        owner.create_agent_session(
            campaign_id="memory",
            session_id="gm",
            role=WriterRole.DM,
            provider_id="fixture",
            model_id="fixture",
        )
        owner.append_agent_message(
            session_id="gm",
            message_id="basis",
            kind=SessionMessageKind.USER,
            content={"text": "Remember the established scene."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="seed-memory",
            turn_id="turn",
            basis_message_ids=("basis",),
            updates=(
                {
                    "operation": "record_memory",
                    "fact_id": "public-fact",
                    "category": "discovery",
                    "text": "The north bell rings in pairs.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
                {
                    "operation": "record_memory",
                    "fact_id": "private-fact",
                    "category": "discovery",
                    "text": "The sealed password is juniper.",
                    "visibility": "gm_private",
                    "known_by": [],
                },
                {
                    "operation": "update_scene",
                    "location_id": "north-belfry",
                    "summary": "Waiting under the bronze bell.",
                },
            ),
        )
        revision = owner.snapshot("memory").revision
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as reopened:
        request = play(reopened, CaptureGM())
        fresh = next(
            m
            for m in request["messages"]
            if str(m.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
        )
        body = json.loads(fresh["content"].split("\n", 1)[1])
        remaining = json.dumps([m for m in request["messages"] if m is not fresh])
        assert "juniper" not in remaining and "north bell rings" not in remaining
        assert body["campaign"]["revision"] == revision
        assert "north bell rings" in json.dumps(body["PLAYER_KNOWN"])
        assert "juniper" in json.dumps(body["GM_PRIVATE_CURRENT"])
        assert "juniper" not in json.dumps(body["PLAYER_KNOWN"])
        assert body["campaign_state"]["current_scene"]["location_id"] == "north-belfry"


def test_real_transport_restores_current_act_progress_and_secret_audiences(tmp_path):
    database = tmp_path / "plan-memory.sqlite3"
    campaign, session, turn = _start_campaign(database, "a-debt-in-iron", character_level=1)
    with AuthorityOwner.open(database, "memory-seed") as owner:
        owner.append_agent_message(
            session_id=session,
            message_id="memory-basis",
            kind=SessionMessageKind.USER,
            content={"text": "Remember what has been established."},
        )
        owner.record_campaign_scene(
            session_id=session,
            scene_id="later-scene",
            act_id="act-2",
            summary="The returned survey has reached the bronze bell.",
            location_id="north-belfry",
            basis_message_ids=("memory-basis",),
            turn_id=turn,
        )
        owner.record_campaign_plan_progress(
            session_id=session,
            update_id="first-objective",
            plan_item_id="objective-1",
            progress_status="resolved",
            summary="The recovery objective was resolved in play.",
            basis_message_ids=("memory-basis",),
            turn_id=turn,
        )
        owner.record_campaign_secret_reveal(
            session_id=session,
            reveal_id="mentor-reveal",
            secret_id="mentor-knew",
            audience=("player",),
            basis_message_ids=("memory-basis",),
            turn_id=turn,
        )
        owner.update_campaign_file(
            session_id=session,
            update_id="transport-facts",
            turn_id=turn,
            basis_message_ids=("memory-basis",),
            updates=(
                {
                    "operation": "record_memory",
                    "fact_id": "returned-cart",
                    "category": "continuity",
                    "text": "A copper owl marks the returned cart.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
                {
                    "operation": "record_memory",
                    "fact_id": "route-token",
                    "category": "continuity",
                    "text": "The hidden route token is marigold-pact.",
                    "visibility": "gm_private",
                    "known_by": [],
                },
            ),
        )
        revision = owner.snapshot(campaign).revision
    provider = CaptureGM()
    harness = _OwnerHarness(database, provider)
    try:
        reply = request_attached(
            harness,
            "recall",
            "submit_session_input",
            {
                "session_id": session,
                "turn_id": turn,
                "expected_revision": revision,
                "attempt_id": "recall",
                "message_id": "recall",
                "user_text": "I wait.",
            },
            campaign_id=campaign,
            attempt_id="recall",
        )
        assert reply["interruption"] is None, reply
    finally:
        harness.close()
    messages = provider.requests[0]["messages"]
    fresh = next(
        m
        for m in messages
        if str(m.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    )
    body = json.loads(fresh["content"].split("\n", 1)[1])
    assert body["campaign_state"]["current_scene"]["act_id"] == "act-2"
    assert body["campaign_state"]["plan_progress"][0]["status"] == "resolved"
    assert body["campaign_state"]["reveals"]["mentor-knew"] == ["player"]
    preparation = next(
        m
        for m in messages
        if str(m.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
    )
    prepared = json.loads(preparation["content"].split("\n", 1)[1])
    assert prepared["campaign_plan_preparation"]["prepared_acts"][0]["id"] == "act-2"
    assert "mentor-knew" in body["PLAYER_KNOWN"]["revealed_secret_ids"]
    assert "rift-is-one-way" not in body["PLAYER_KNOWN"]["revealed_secret_ids"]
    assert "copper owl" in json.dumps(body["PLAYER_KNOWN"])
    assert "marigold-pact" in json.dumps(body["GM_PRIVATE_CURRENT"])
    assert "marigold-pact" not in json.dumps(body["PLAYER_KNOWN"])
    history = json.dumps([m for m in messages if m.get("role") in {"assistant", "tool"}])
    assert "bronze bell" not in history and "recovery objective was resolved" not in history
    assert "copper owl" not in history and "marigold-pact" not in history
