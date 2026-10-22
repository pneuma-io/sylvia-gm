"""Literal compact effects retain the runtime language through ordinary package admission."""

import hashlib
import json
from copy import deepcopy

import pytest
import yaml

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.application.package_runtime import PackageRuntimeError, validate_package_runtime
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain._content_schema_ruleset import EFFECT_FIELDS
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime._authoring_declarations import EFFECT_BODY_FIELDS
from sylvia_gm.runtime._effect_executor import _EFFECT_HANDLERS

EFFECT_EXAMPLES = {
    "publish": {"publish": {"fields": {"result": "_result"}}},
    "shuffle": {"shuffle": {"collection": "state.cards"}, "store_as": "_cards"},
    "dice": {"dice": {"expression": "1d6"}, "store_as": "_roll"},
    "set": {"set": {"field": "state.ready", "value": True}},
    "set_participant": {"set_participant": {"participant": "actor", "field": "hp", "value": 5}},
    "create_entity": {"create_entity": {"id": "signal", "value": {"kind": "signal"}}},
    "require": {"require": {"condition": "true", "error": "Signal unavailable."}},
    "request_choice": {
        "request_choice": {
            "choice_id": "route",
            "prompt": "Choose a route.",
            "options": [{"id": "north", "label": "North"}, {"id": "south", "label": "South"}],
        },
        "store_as": "_route",
    },
    "apply_damage": {"apply_damage": {"target": "actor", "amount": 1}},
    "apply_condition": {"apply_condition": {"target": "actor", "condition": "ready"}},
    "remove_condition": {"remove_condition": {"target": "actor", "condition": "ready"}},
    "call": {"call": "helper", "params": {}, "export_context": {"_child": "_result"}},
    "trigger": {"trigger": "on_ready", "context": {"signal": "ready"}},
    "save_throw": {"save_throw": {"target": "actor", "ability": "dex", "dc": 10}},
    "heal": {"heal": {"target": "actor", "amount": 1}},
    "decrement": {"decrement": {"field": "state.count", "amount": 1}},
    "increment": {"increment": {"field": "state.count", "amount": 1}},
    "condition": {
        "condition": "true",
        "on_true": [{"set": {"field": "_result", "value": 1}}],
        "on_false": [{"set": {"field": "_result", "value": 0}}],
    },
    "log": {"log": {"level": "info", "message": "Ready."}},
    "table_lookup": {"table_lookup": {"table": "signals", "key": "ready", "default": 0}},
    "spatial_query": {
        "spatial_query": {"scene": "state.scene", "operation": "snapshot"},
        "store_as": "_snapshot",
    },
    "spatial_mutation": {
        "spatial_mutation": {
            "scene": "state.scene",
            "map_id": "map",
            "expected_revision": 0,
            "target": {"kind": "geometry", "id": "gate"},
            "patch": {"active": False},
        },
        "store_as": "_mutation",
    },
    "shot_effect": {
        "shot_effect": {
            "scene": "state.scene",
            "snapshot": "{_snapshot}",
            "source_id": "actor",
            "target_id": "target",
            "origin": [0, 0],
            "destination": [1, 0],
            "delay_ms": 0,
            "miss": False,
            "contacts": [],
        }
    },
    "for_each": {"for_each": {"collection": "state.cards", "as": "card", "do": []}},
    "sort": {"sort": {"collection": "state.cards", "by": "rank", "direction": "asc"}},
    "apply_ability_damage": {
        "apply_ability_damage": {"target": "actor", "ability": "str", "amount": 1}
    },
    "count_successes": {"count_successes": {"pool": 2, "threshold": 6}},
    "reroll": {"reroll": {"source": "_roll", "predicate": "true", "consequence": []}},
    "hunger_dice": {"hunger_dice": {"pool": 2, "hunger": 1, "sides": 10, "threshold": 6}},
    "set_flag": {"set_flag": {"flag": "ready", "value": True}},
    "advance_time": {"advance_time": {"duration": 1}},
    "list_push": {"list_push": {"list": "state.cards", "value": "ace"}},
    "list_pop": {"list_pop": {"list": "state.cards"}},
    "initiative": {"initiative": {"combatants": "state.actors", "expression": "1d20"}},
    "advance_initiative": {"advance_initiative": {"skip_field": "hp", "skip_equals": 0}},
    "random_table": {"random_table": {"table": "signals", "roll": "1d6"}},
    "transition_scene": {"transition_scene": {"scene": "relay"}},
    "aoe": {"aoe": {"aoe": "state.actors", "as": "actor", "do": []}},
    "defer": {"defer": {"tool": "helper", "params": {}, "reason": "Follow up."}},
    "narrate": {"narrate": {"text": "The relay is ready."}},
    "register_initiative_trigger": {
        "register_initiative_trigger": {"id": "signal", "procedure": "ready", "count": 1}
    },
    "clock_create": {"clock_create": {"id": "signal", "name": "Signal", "max_segments": 4}},
    "clock_tick": {"clock_tick": {"id": "signal", "segments": 1}},
}


def _source(effect):
    source = get_content_definition("ruleset_source")["example"]
    source["mechanic_families"]["flip"]["effects"] = [effect]
    source["private_mechanics"] = [
        {"name": "helper", "family": "helper", "description": "Helper", "configure": {}}
    ]
    source["mechanic_families"]["helper"] = {
        "parameters": [],
        "pre_conditions": [],
        "effects": [{"set": {"field": "_result", "value": 1}}],
        "narrative": {},
    }
    return source


def _ready_procedures():
    return {
        "ready": {
            "trigger": "on_ready",
            "steps": [{"set": {"field": "state.signalled", "value": True}}],
            "narrative": {},
        }
    }


def test_literal_examples_cover_dispatch_and_declarations_separately_from_siblings():
    assert set(EFFECT_EXAMPLES) == set(_EFFECT_HANDLERS)
    assert set(EFFECT_EXAMPLES) == set(EFFECT_BODY_FIELDS)
    siblings = {"params", "export_context", "context", "on_true", "on_false", "store_as"}
    assert not siblings & set(_EFFECT_HANDLERS)
    assert set(EFFECT_FIELDS) == set(EFFECT_EXAMPLES) | siblings


@pytest.mark.parametrize("handler,effect", EFFECT_EXAMPLES.items(), ids=EFFECT_EXAMPLES)
def test_literal_compact_effect_validates_compiles_and_installs(tmp_path, handler, effect):
    source = _source(effect)
    if handler == "trigger":
        source["procedures"] = _ready_procedures()
    validate_content_shape("ruleset_source", source, file="ruleset-source.yaml")
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    runtime = yaml.safe_load(compiled.draft.payloads["runtime/ruleset.yaml"])
    assert runtime["tools"]["flip_coin"]["effects"] == [effect]
    assert runtime["procedures"] == source.get("procedures", {})
    with AuthorityOwner.open(tmp_path / "owner.db", "effect-admission") as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id=handler)
        assert candidate.package_lock.entries[0].package_id == source["id"]


@pytest.mark.parametrize("procedures", [None, {"other": {"trigger": "on_other", "steps": []}}])
def test_compact_trigger_requires_a_matching_procedure_at_runtime_admission(procedures):
    source = _source(EFFECT_EXAMPLES["trigger"])
    if procedures is not None:
        source["procedures"] = procedures
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    with pytest.raises(PackageRuntimeError, match="trigger has no matching procedure"):
        validate_package_runtime(
            package_id=compiled.draft.record["id"],
            declarations=(),
            runtime_bytes=compiled.draft.payloads["runtime/ruleset.yaml"],
        )


@pytest.mark.parametrize(
    "name", ["modify_field", "conditional", "nested_call", "trigger_procedure", "execute_python"]
)
def test_unsupported_literal_names_fail_at_compact_source_with_diagnostics(name):
    source = _source({name: {}})
    with pytest.raises(ContentShapeError) as shape:
        validate_content_shape("ruleset_source", source, file="ruleset-source.yaml")
    assert shape.value.diagnostics[0].path.startswith("$.mechanic_families.flip.effects[0]")
    assert shape.value.diagnostics[0].code == "content_enum_invalid"
    assert shape.value.diagnostics[0].guidance == (
        "Read the ruleset_source content definition and correct this field."
    )
    with pytest.raises(CommandRejected) as compiled:
        compile_ruleset_source(yaml.safe_dump(source).encode())
    assert compiled.value.diagnostics == shape.value.diagnostics
    assert name not in _EFFECT_HANDLERS


@pytest.mark.parametrize(
    "effect,suffix",
    [
        ({"set": {"field": "state.x", "value": 1, "guess": True}}, ".set.guess"),
        ({"call": "helper", "context": {}}, ".context"),
        ({"trigger": "on_ready", "params": {}}, ".params"),
        ({"condition": "true", "on_true": [{"log": {"guess": 1}}]}, ".on_true[0].log.guess"),
        ({"log": "ignored"}, ".log"),
    ],
)
def test_compilation_preserves_bodies_for_existing_runtime_rejection(effect, suffix):
    compiled = compile_ruleset_source(yaml.safe_dump(_source(effect)).encode())
    runtime_bytes = compiled.draft.payloads["runtime/ruleset.yaml"]
    assert yaml.safe_load(runtime_bytes)["tools"]["flip_coin"]["effects"] == [effect]
    with pytest.raises(PackageRuntimeError) as rejected:
        validate_package_runtime(
            package_id=compiled.draft.record["id"], declarations=(), runtime_bytes=runtime_bytes
        )
    assert f"$.tools.flip_coin.effects[0]{suffix}" in {
        item.path for item in rejected.value.diagnostics
    }
    assert all(item.guidance for item in rejected.value.diagnostics)


def test_family_tokens_expand_nested_call_trigger_and_flat_branches_unchanged(tmp_path):
    expected = [
        {"call": "helper", "params": {}, "export_context": {"_child": "_result"}},
        {"trigger": "on_ready", "context": {"value": "{_child}"}},
        {
            "condition": "_child == 1",
            "on_true": [{"set": {"field": "state.ready", "value": True}}],
            "on_false": [{"require": {"condition": "false", "error": "Not ready."}}],
        },
    ]
    source = _source({"set": {"field": "state.ready", "value": False}})
    source["mechanic_families"]["flip"]["effects"] = "{steps}"
    source["public_actions"][0]["configure"] = {"steps": expected}
    source["procedures"] = _ready_procedures()
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    raw = yaml.safe_load(compiled.draft.payloads["runtime/ruleset.yaml"])
    assert raw["tools"]["flip_coin"]["effects"] == expected
    with (
        materialize_package_archive((compiled.draft,)) as artifact,
        AuthorityOwner.open(tmp_path / "tokens.db", "tokens") as owner,
    ):
        owner.install_package_archive(artifact.archive, install_id="tokens")


def test_compact_procedures_execute_nested_branches_calls_and_triggers(tmp_path):
    source = _source({"trigger": "on_ready", "context": {"amount": 3}})
    source["state_schemas"] = [
        {
            "key": "state",
            "schema": {"type": "object"},
            "references": [],
            "visibility": "engine",
        }
    ]
    source["mechanic_families"]["helper"]["parameters"] = [
        {
            "name": "amount",
            "type": "integer",
            "source": "choice",
            "required": True,
        }
    ]
    source["mechanic_families"]["helper"]["effects"] = [
        {"increment": {"field": "state.count", "amount": "amount"}},
    ]
    source["public_actions"][0]["configure"] = {"label": "not substituted"}
    source["procedures"] = {
        "ready": {
            "trigger": "on_ready",
            "condition": "amount > 0",
            "steps": [
                {
                    "condition": "amount == 3",
                    "on_true": [
                        {"call": "helper", "params": {"amount": "{amount}"}},
                        {"trigger": "on_finished"},
                    ],
                    "on_false": [{"require": {"condition": "false", "error": "Wrong amount."}}],
                }
            ],
            "narrative": {"template": "{label}"},
        },
        "finished": {
            "trigger": "on_finished",
            "steps": [{"set": {"field": "state.finished", "value": True}}],
        },
    }
    expected = deepcopy(source["procedures"])
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    raw = yaml.safe_load(compiled.draft.payloads["runtime/ruleset.yaml"])
    assert raw["procedures"] == expected
    links = json.loads(compiled.draft.payloads["content/content-declarations.json"])["inclusions"][
        0
    ]["links"]
    assert links["procedures"] == ["finished", "ready"]
    with AuthorityOwner.open(tmp_path / "procedure.db", "procedure-test") as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="procedure")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="procedure",
                display_name="Procedure",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                initial_state=(StateValue(source["id"], "state", {"count": 0, "finished": False}),),
            )
        )
        receipt = owner.execute_action(
            ActionCommand(
                campaign_id="procedure",
                action_id="trigger",
                turn_id="turn",
                expected_revision=0,
                package_id=source["id"],
                tool_name="flip_coin",
                parameters={},
                idempotency_key="trigger",
                owner_id="procedure-test",
                writer=WriterRole.DM,
            )
        )
        assert receipt.status.value == "committed", receipt
        state = thaw_json_value(owner.snapshot("procedure").state)
        assert state[f"{source['id']}.state"] == {"count": 3, "finished": True}


@pytest.mark.parametrize(
    "procedure,suffix",
    [
        ({"trigger": "on_ready", "guess": True}, ".guess"),
        ({"trigger": "on_ready", "steps": [42]}, ".steps[0]"),
        ({"trigger": "on_ready", "steps": "{steps}"}, ".steps"),
    ],
)
def test_compact_procedure_shapes_reject_with_source_paths(procedure, suffix):
    source = _source(EFFECT_EXAMPLES["trigger"])
    source["procedures"] = {"ready": procedure}
    with pytest.raises(CommandRejected) as rejected:
        compile_ruleset_source(yaml.safe_dump(source).encode())
    assert f"$.procedures.ready{suffix}" in {item.path for item in rejected.value.diagnostics}


@pytest.mark.parametrize(
    "procedure",
    [
        {"trigger": "on_ready", "condition": "eval('x')", "steps": []},
        {"trigger": "on_ready", "steps": [{"set": {"field": "state.x", "value": 1, "guess": 2}}]},
        {"trigger": "on_ready", "steps": [{"log": "ignored"}]},
        {"trigger": "on_ready", "steps": [{"conditional": {"condition": "true"}}]},
        {"trigger": "on_ready", "steps": [{"nested_call": "helper"}]},
        {"trigger": "on_ready", "steps": [{"trigger_procedure": "on_finished"}]},
        {"trigger": "on_ready", "steps": [{"modify_field": {"field": "state.x", "value": 1}}]},
        {"trigger": "on_ready", "steps": [{"condition": "eval('x')", "on_true": []}]},
        {"trigger": "on_ready", "steps": [{"condition": "true", "on_true": [{"call": "missing"}]}]},
    ],
)
def test_compact_procedure_executables_reject_at_ordinary_admission(tmp_path, procedure):
    source = _source(EFFECT_EXAMPLES["trigger"])
    source["procedures"] = {"ready": procedure}
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    with AuthorityOwner.open(tmp_path / "invalid-procedure.db", "invalid-procedure") as owner:
        with (
            materialize_package_archive((compiled.draft,)) as artifact,
            pytest.raises(CommandRejected),
        ):
            owner.install_package_archive(artifact.archive, install_id="invalid-procedure")
        assert owner.package_candidates() == ()


@pytest.mark.parametrize(
    "step,features",
    [
        (
            {"shuffle": {"collection": "state.cards"}, "store_as": "_cards"},
            {"collection_mechanics.v1"},
        ),
        ({"publish": {"fields": {"ready": "_ready"}}}, {"public_results.v1"}),
        (
            {
                "spatial_query": {"scene": "state.scene", "operation": "snapshot"},
                "store_as": "_scene",
            },
            {"tactical_grid.v1"},
        ),
    ],
)
def test_procedure_only_features_and_inventory_are_discovered(step, features):
    source = _source({"set": {"field": "_ready", "value": True}})
    source["procedures"] = {
        "ready": {
            "trigger": "on_ready",
            "steps": [{"condition": "true", "on_true": [step]}],
        }
    }
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    assert set(compiled.required_engine_features) == features
    links = json.loads(compiled.draft.payloads["content/content-declarations.json"])["inclusions"][
        0
    ]["links"]
    assert links["procedures"] == ["ready"]


def test_no_procedure_source_retains_its_previous_runtime_bytes():
    source = get_content_definition("ruleset_source")["example"]
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    runtime = compiled.draft.payloads["runtime/ruleset.yaml"]
    assert yaml.safe_load(runtime)["procedures"] == {}
    assert {
        name: hashlib.sha256(data).hexdigest() for name, data in compiled.draft.payloads.items()
    } == {
        "runtime/ruleset.yaml": "ac0da87063ac39ada84f248beaf5e3bdaf9c5e57b233cd2e08c7817893fe12aa",
        "support/gameplay-resolution.json": (
            "38c8e69f5142e2898fb06a8ecc4387bbad15b3a8c0d729391ac85f1c76b96af0"
        ),
        "content/content-declarations.json": (
            "135fb0614117e2b2e414eeb0785e5bb7fb23b3b0bdd3f00ad054bb6f2271a97b"
        ),
    }
    assert hashlib.sha256(canonical_json_bytes(compiled.draft.record)).hexdigest() == (
        "4057df5d0767328dc54ff152e6c65561a2a4daf503f4d63cae7f1f089cc5824b"
    )
    source["procedures"] = {}
    assert (
        compile_ruleset_source(yaml.safe_dump(source).encode()).draft.payloads[
            "runtime/ruleset.yaml"
        ]
        == runtime
    )
