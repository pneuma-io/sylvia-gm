"""The GM is shown only Ruleset tools the current campaign can use; authority is unchanged."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from scripts.update_srd_tool_exposure import EXPOSURE, NOT_IN_LITE, SPELL_SCOPES, annotate

from sylvia_gm.agent._stateless_mcp import _model_input_schema
from sylvia_gm.agent._tool_exposure import (
    EXPOSURE_KEY,
    SPELL_SCOPE_KEY,
    ExposureContext,
    exposure_conditions,
    scoped_spell_choices,
)
from sylvia_gm.application.campaign_rules_update import _tool_input_change
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration

LITE = Path(__file__).resolve().parents[1] / "rulesets/srd-lite"
NS = "sylvia.srd-lite.bg3"


def _schema(*conditions):
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
        EXPOSURE_KEY: {"any_of": list(conditions)},
    }


def _context(state, castable=()):
    return ExposureContext(
        state,
        castable_spell_ids=frozenset(castable),
        asked_spell_ids=frozenset({"web", "find_steed", "geas"}),
    )


@pytest.fixture(scope="module")
def record():
    return json.loads((LITE / "package-record.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def definitions():
    runtime = yaml.load(
        (LITE / "runtime/ruleset.yaml").read_text(encoding="utf-8"), Loader=yaml.CSafeLoader
    )
    return runtime["definitions"]


def test_unannotated_or_unknown_conditions_are_always_shown():
    context = _context({})
    assert context.exposed({"type": "object", "properties": {}})
    assert context.exposed(_schema({"future_kind": ["x"]}))
    assert exposure_conditions(_schema({"spells": []})) is None
    assert context.exposed(_schema({"entity_field": "hp", "op": "between"}))


def test_spell_condition_holds_when_castable_or_left_in_state():
    schema = _schema({"spells": ["web"]})
    assert not _context({}).exposed(schema)
    assert _context({}, castable={"web"}).exposed(schema)
    # A lasting effect keeps its spell id after its caster is gone.
    state = {f"{NS}.entities": {"lasting": {"sources": [{"spell_id": "web"}]}}}
    assert _context(state).exposed(schema)
    assert not _context({f"{NS}.entities": {"a": {"note": "a web of lies"}}}).exposed(schema)


def test_entity_field_operators_match_the_runtime_refusals():
    entities = {
        "fighter": {"resources": {"second_wind": 0}, "summon_active": False},
        "monk": {"resources": {"wholeness_of_body": 2}, "form_current_source_id": None},
    }
    context = _context({f"{NS}.entities": entities})
    assert not context.exposed(_schema({"entity_field": "resources.second_wind", "op": "gte1"}))
    assert context.exposed(_schema({"entity_field": "resources.wholeness_of_body", "op": "gte1"}))
    assert not context.exposed(_schema({"entity_field": "summon_active", "op": "true"}))
    form = {"entity_field": "form_current_source_id", "op": "not_null"}
    assert not context.exposed(_schema(form))
    # Only *.entities roots are read for entity fields.
    other = _context({f"{NS}.combat": {"x": {"summon_active": True}}})
    assert not other.exposed(_schema({"entity_field": "summon_active", "op": "true"}))


def test_spell_scope_narrows_choices_and_keeps_aliases():
    enum = ["shield", "counterspell", "cure_wounds", "hunters_mark", "hunter_s_mark"]
    aliases = {"hunter_s_mark": "hunters_mark"}
    assert scoped_spell_choices(
        enum, castable_spell_ids=frozenset({"shield", "cure_wounds"}),
        scope=["shield", "counterspell"], aliases=aliases,
    ) == ["shield"]
    assert scoped_spell_choices(
        enum, castable_spell_ids=frozenset({"hunters_mark"}), scope=None, aliases=aliases,
    ) == ["hunters_mark", "hunter_s_mark"]
    # No castable set and no scope leaves the declared list alone.
    assert scoped_spell_choices(enum, castable_spell_ids=None, scope=None, aliases={}) == enum


def test_model_schema_drops_presentation_annotations():
    schema = _schema({"spells": ["web"]})
    schema["properties"]["spell"] = {
        "type": "string",
        "enum": ["shield", "cure_wounds"],
        "x-sylvia-gm-castable-spells": True,
        SPELL_SCOPE_KEY: ["shield"],
    }
    projected, _ = _model_input_schema(
        schema, participant_ids=(), castable_spell_ids=frozenset({"shield", "cure_wounds"})
    )
    assert EXPOSURE_KEY not in projected
    assert SPELL_SCOPE_KEY not in projected["properties"]["spell"]
    assert projected["properties"]["spell"]["enum"] == ["shield"]


def test_checked_in_record_matches_the_exposure_table(record, definitions):
    rebuilt = copy.deepcopy(record)
    annotate(rebuilt, definitions)
    assert rebuilt == record


def test_table_spells_are_lite_spells_unless_marked_outside_lite(definitions):
    lite = set(definitions["spell_casting"])
    for name, entry in EXPOSURE.items():
        spells = {s for c in entry["any_of"] for s in c.get("spells", ())}
        if NOT_IN_LITE in str(entry["reason"]):
            assert spells and not spells & lite, name
        else:
            assert spells <= lite | {"irresistible_dance", "clairvoyance", "create_undead"}, name
    for name, entry in SPELL_SCOPES.items():
        assert set(entry.get("spells", ())) <= lite | {
            "searing_smite", "shining_smite", "ensnaring_strike"
        }, name


def test_scoped_casting_tools_keep_spells_their_refusals_accept(record, definitions):
    tools = {tool["name"]: tool for tool in record["tools"]}
    times = {s: row["casting_time"] for s, row in definitions["spells"].items()}
    spell = tools["begin_srd_spell_action"]["input_schema"]["properties"]["spell"]
    action = set(spell[SPELL_SCOPE_KEY])
    assert {s for s in action if times[s] not in {"action", "bonus_action", "reaction"}} == set()
    assert "mending" not in action and "cure_wounds" in action
    reaction = tools["cast_srd_reaction_spell"]["input_schema"]["properties"]["spell"]
    assert reaction[SPELL_SCOPE_KEY] == ["counterspell", "shield"]
    smite = tools["begin_srd_after_hit_smite"]["input_schema"]["properties"]["spell"]
    assert smite[SPELL_SCOPE_KEY] == ["divine_smite"]


def test_existing_campaigns_accept_the_annotations_as_model_guidance(record):
    tool = next(t for t in record["tools"] if t["name"] == "cast_srd_reaction_spell")
    incoming = copy.deepcopy(tool["input_schema"])
    prior = copy.deepcopy(incoming)
    prior["properties"]["spell"].pop(SPELL_SCOPE_KEY)

    def declaration(schema):
        return ToolDeclaration(
            name=tool["name"], capability=tool["capability"],
            input_schema=owned_payload(schema), writers=("dm",), turn_statuses=("open",),
            state_predicates=(),
        )

    assert _tool_input_change(declaration(prior), declaration(incoming)) == "changed_model_guidance"
    steed = next(t for t in record["tools"] if t["name"] == "mount_srd_otherworldly_steed")
    incoming = copy.deepcopy(steed["input_schema"])
    prior = copy.deepcopy(incoming)
    prior.pop(EXPOSURE_KEY)
    assert _tool_input_change(declaration(prior), declaration(incoming)) == "changed_model_guidance"
