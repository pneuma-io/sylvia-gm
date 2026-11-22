"""Generate reference vectors for the provider tool projection (lane 4, P2).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_tool_projection_vectors.py

Writes `tool_projection_vectors.json`:

- `aliases`: `_package_tool_alias` for a list of identities;
- `tools`: for each `ModelMechanicsTool`, `_tool_description`, `_model_tool_schema` (with the
  argument-contract envelope, and without it for a tool that declares contracts), or the
  exception each raises;
- `catalogs`: `_provider_tools` over a list of tools, giving the tool list (in full, or as the
  SHA-256 of its compact JSON for the large catalogue, whose tools are the first
  `first_tools` of `tools`) and the aliases, or the exception;
- `resolve`: `_resolve_provider_tool_alias` against one catalogue's aliases.

The tools are every SRD Lite package tool, passed through the catalog's own model projection
(`_model_input_schema`, as the MCP listing gives it, and its fixed-list form for the two tools
with argument contracts), the application's action-sequence tools, the Campaign File update,
and synthetic tools for each branch and refusal. Reference tooling only; retired with the
Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _provider_session as session  # noqa: E402
from sylvia_gm.agent import _stateless_mcp as mcp  # noqa: E402
from sylvia_gm.agent._argument_contracts import CONTRACT_KEY  # noqa: E402
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool  # noqa: E402
from sylvia_gm.domain.authority import ModelToolBehavior  # noqa: E402

OUTPUT = Path(__file__).with_name("tool_projection_vectors.json")
BEHAVIORS = list(ModelToolBehavior)
SHA = "ab" * 32


def failure(error: BaseException) -> dict[str, object]:
    return {"error": {"class": type(error).__name__, "message": str(error),
                      "code": getattr(error, "code", None)}}


def attempt(function: Any, *args: Any, **kwargs: Any) -> dict[str, object]:
    try:
        return {"value": function(*args, **kwargs)}
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return failure(error)
    except Exception as error:  # CommandRejected and the other authority errors
        return failure(error)


def tool_fields(tool: ModelMechanicsTool) -> dict[str, object]:
    return {
        "package_id": tool.package_id, "package_version": tool.package_version,
        "package_sha256": tool.package_sha256, "name": tool.name, "capability": tool.capability,
        "input_schema": tool.input_schema, "parameter_derivations": tool.parameter_derivations,
        "description": tool.description, "category": tool.category,
        "model_behavior": tool.model_behavior.value,
    }


def make(name: str, schema: dict[str, object], **fields: Any) -> ModelMechanicsTool:
    return ModelMechanicsTool(
        package_id=fields.pop("package_id", "test.package"),
        package_version=fields.pop("package_version", "1.0.0"),
        package_sha256=fields.pop("package_sha256", SHA),
        name=name, capability=fields.pop("capability", f"test.{name}"),
        input_schema=schema, **fields,
    )


def srd_tools() -> list[ModelMechanicsTool]:
    record = json.loads((ROOT / "rulesets/srd-lite/package-record.json").read_text("utf-8"))
    tools = []
    for index, raw in enumerate(record["tools"]):
        fixed = CONTRACT_KEY in raw["input_schema"] and index % 2 == 0
        schema, derivations = mcp._model_input_schema(  # noqa: SLF001
            raw["input_schema"], participant_ids=("pc-aria", "npc-goblin-1"), fixed=fixed)
        behavior = BEHAVIORS[index % len(BEHAVIORS)]
        if "player_intent_after_preparation" in schema.get("properties", {}):
            behavior = ModelToolBehavior.OPTIONAL
        tools.append(ModelMechanicsTool(
            package_id=record["id"], package_version=record["version"],
            package_sha256=record["content_sha256"], name=raw["name"],
            capability=raw["capability"], input_schema=schema,
            parameter_derivations=derivations, description=raw.get("description", "") or "",
            category="mechanic", model_behavior=behavior,
        ))
    return tools


class _QueueOwner:
    def action_sequence(self) -> None: ...
    def campaign_panels(self) -> None: ...
    def _compose_action_sequence_from_agent(self) -> None: ...
    def _execute_action_sequence_from_agent(self) -> None: ...


def application_tools() -> list[ModelMechanicsTool]:
    tools = list(mcp.action_sequence_model_tools(_QueueOwner()))  # type: ignore[arg-type]
    operations = [operation for operation, _ in mcp._CAMPAIGN_FILE_COLLECTIONS]  # noqa: SLF001
    for chosen in (operations, ["record_memory", "update_scene"]):
        tools.append(ModelMechanicsTool(
            package_id="sylvia.application", package_version="1", package_sha256="0" * 64,
            name="update_campaign_file", capability="narrative_state.update_campaign_file",
            input_schema=mcp._campaign_file_update_schema({  # noqa: SLF001
                "operations": chosen, "scene_act_ids": ["act-1"], "plan_item_ids": ["goal-1"],
                "eligible_secret_ids": ["secret-1"],
                "character_update_schema": {"type": "object", "properties": {
                    "participant_id": {"type": "string",
                                       "x-sylvia-gm-reference": "participant"}}}}),
            description=mcp._campaign_file_update_description(),  # noqa: SLF001
            category="narrative_state",
        ))
    tools.append(ModelMechanicsTool(
        package_id="sylvia.application", package_version="1", package_sha256="0" * 64,
        name="read_campaign_reference", capability="context.read", category="reference",
        description="Read one exact reference.",
        input_schema={"type": "object", "properties": {
            "source": {"type": "string", "enum": ["campaign_state", "engine_format"]},
            "path": {"type": "string"}}, "required": ["source", "path"],
            "additionalProperties": False},
    ))
    return tools


def contract(**overrides: object) -> dict[str, object]:
    declaration: dict[str, object] = {
        "version": 1, "selector": "spell", "field": "spell_arguments",
        "common": ["caster", "spell", "target", "missing_common"],
        "by_value": {"fire_bolt": ["target", "caster", "missing"], "shield": [],
                     "bless": ["targets", "spell"]},
    }
    declaration.update(overrides)
    return declaration


def contract_schema(declaration: object = None, **schema_overrides: object) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "description": "Cast a spell.",
        CONTRACT_KEY: contract() if declaration is None else declaration,
        "properties": {
            "caster": {"type": "string", "x-sylvia-gm-reference": "participant"},
            "spell": {"type": "string", "enum": ["fire_bolt", "shield", "bless", "Fire Bolt"],
                      "x-sylvia-gm-value-aliases": {"Fire Bolt": "fire_bolt", "Shield": 3,
                                                    "Bless": None, "List": [1, "a"],
                                                    "Flag": True, "Half": 0.5}},
            "target": {"$ref": "#/$defs/target"},
            "targets": {"type": "array", "items": {"$ref": "#/$defs/target"}},
            "slot": {"$ref": "#/$defs/slot"},
        },
        "required": ["caster", "spell", "target"],
        "additionalProperties": False,
        "$defs": {"target": {"type": "string", "x-sylvia-gm-reference": "participant"},
                  "slot": {"type": "integer", "minimum": 1}, "unused": {"type": "null"}},
    }
    schema.update(schema_overrides)
    return schema


def synthetic_tools() -> list[ModelMechanicsTool]:
    leaves = ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING
    references = {
        "type": "object",
        "properties": {
            "actor": {"type": "string", "x-sylvia-gm-reference": "participant"},
            "named": {"type": "string", "x-sylvia-gm-reference": "participant",
                      "description": "Kept."},
            "nulled": {"type": "string", "x-sylvia-gm-reference": "entity", "description": None},
            "thing": {"x-sylvia-gm-reference": "entity"},
            "actor_state": {"type": "string"},
            "count_state": {"type": "integer"},
            "use_feature": {"type": "boolean"},
            "use_count": {"type": "integer"},
            "use_state": {"type": "string"},
            "weird": "not a schema",
            "listed": [1, 2],
            "ünïcode_state": {"type": "string"},
        },
        "required": ["actor", 3, None, ["x"]],
    }
    return [
        make("plain", {"type": "object", "properties": {}}),
        make("described", {"type": "object"}, description="  Roll a die.\n"),
        make("same_as_capability", {"type": "object", "description": " Schema text. "},
             description="test.same_as_capability"),
        make("blank", {"type": "object", "description": "\x1c　 "}, description="\x1f \x85"),
        make("needs_inputs", {"type": "object", "required": ["a", 2, 1.5, True, None,
                                                            {"k": "v"}, ["l"]]}),
        make("required_mapping", {"type": "object", "required": {"a": 1}}),
        make("required_text", {"type": "object", "required": "ab"}),
        make("required_empty", {"type": "object", "required": []}),
        make("references", references),
        make("properties_not_object", {"type": "object", "properties": ["a"]},
             model_behavior=leaves),
        make("no_properties", {"type": "object"}, model_behavior=leaves),
        make("prepares", {"type": "object", "properties": {"x": {"type": "string"}}},
             model_behavior=leaves),
        make("collides", {"type": "object", "properties": {
            "player_intent_after_preparation": {"type": "string"}}}, model_behavior=leaves),
        make("update_campaign_file", {"type": "object", "properties": {
            "revised_player_facing_response": {"type": "string"}, "after": {}}},
            category="narrative_state", capability="narrative_state.update_campaign_file",
            description="Update it."),
        make("wrong_category_file", {"type": "object", "properties": {}},
             capability="narrative_state.update_campaign_file"),
        make("both_additions", {"type": "object", "properties": {}}, model_behavior=leaves,
             category="narrative_state", capability="narrative_state.update_campaign_file"),
        make("cast", contract_schema()),
        make("cast_prepares", contract_schema(), model_behavior=leaves),
        make("contract_true_version", contract_schema(contract(version=True))),
        make("contract_float_version", contract_schema(contract(version=1.0))),
        make("contract_version_2", contract_schema(contract(version=2))),
        make("contract_text_version", contract_schema(contract(version="1"))),
        make("contract_no_version", contract_schema({k: v for k, v in contract().items()
                                                     if k != "version"})),
        make("contract_null", contract_schema(None, **{CONTRACT_KEY: None})),
        make("contract_list", contract_schema(["version"])),
        make("contract_bad_selector", contract_schema(contract(selector="nope"))),
        make("contract_selector_number", contract_schema(contract(selector=1))),
        make("contract_field_declared", contract_schema(contract(field="target"))),
        make("contract_common_text", contract_schema(contract(common="caster"))),
        make("contract_common_numbers", contract_schema(contract(common=["caster", 1]))),
        make("contract_by_value_list", contract_schema(contract(by_value=["x"]))),
        make("contract_by_value_entry", contract_schema(contract(
            by_value={"shield": [], "fire_bolt": "target"}))),
        make("contract_no_properties", {CONTRACT_KEY: contract()}),
        make("contract_required_text", contract_schema(required="caster")),
        make("contract_required_mapping", contract_schema(required={"spell": 1, "x": 2})),
        make("contract_required_null", contract_schema(required=None)),
        make("contract_required_number", contract_schema(required=7)),
        make("contract_required_nested", contract_schema(required=["spell", ["caster"]])),
        make("contract_required_values", contract_schema(required=[1, None, True, "caster"])),
        make("contract_no_required", {k: v for k, v in contract_schema().items()
                                      if k != "required"}),
        make("contract_defs_dangling", contract_schema(**{"$defs": {"slot": {}}})),
        make("contract_defs_not_object", contract_schema(**{"$defs": ["target"]})),
        make("contract_defs_unused", contract_schema(contract(common=["spell"]))),
        make("contract_defs_chain", contract_schema(**{"$defs": {
            "target": {"anyOf": [{"$ref": "#/$defs/name"}, {"$ref": "#/$defs/target"}]},
            "name": {"type": "string", "items": [{"$ref": "#/$defs/deep"}]},
            "deep": {"$ref": "#/defs/elsewhere"}, "zeta": {}}})),
        make("contract_selector_not_object", contract_schema(properties={
            "caster": {"type": "string"}, "spell": "text"})),
        make("contract_aliases_list", contract_schema(properties={
            "caster": {}, "spell": {"x-sylvia-gm-value-aliases": ["Fire Bolt"]}})),
        make("contract_key_order", {"$defs": {"target": {}}, "required": ["spell"],
                                    "properties": {"target": {"$ref": "#/$defs/target"},
                                                   "spell": {"type": "string"}},
                                    CONTRACT_KEY: contract(common=["target"]),
                                    "type": "object", "zzz": 1}),
        make("Bolt ⚡ é!", {"type": "object", "properties": {}}, package_id="pkg-é😀"),
        make("!!!", {"type": "object", "properties": {}}),
        make("x" * 90, {"type": "object", "properties": {}}),
    ]


def tool_case(tool: ModelMechanicsTool) -> dict[str, object]:
    case: dict[str, object] = {
        "tool": tool_fields(tool),
        "description": attempt(session._tool_description, tool),  # noqa: SLF001
        "schema": attempt(session._model_tool_schema, tool),  # noqa: SLF001
    }
    if CONTRACT_KEY in tool.input_schema:
        case["full_schema"] = attempt(session._model_tool_schema, tool,  # noqa: SLF001
                                      envelope=False)
    return case


def provider_tools(catalog: list[ModelMechanicsTool], *, digest: bool) -> dict[str, object]:
    adapter = object.__new__(session.ProviderSessionAdapter)
    before = copy.deepcopy([tool.input_schema for tool in catalog])
    try:
        tools, aliases = adapter._provider_tools(tuple(catalog))  # noqa: SLF001
    except Exception as error:
        return failure(error)
    assert before == [tool.input_schema for tool in catalog], "projection changed its input"
    listed: object = tools
    if digest:
        text = json.dumps(tools, ensure_ascii=True, separators=(",", ":"))
        listed = {"count": len(tools), "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return {"value": {"tools": listed,
                      "aliases": [[alias, tool.name] for alias, tool in aliases.items()]}}


def main() -> None:
    srd = srd_tools()
    application = application_tools()
    # The second Campaign File update differs only in its options: one catalogue lists one.
    listed = [tool for index, tool in enumerate(application) if index != 5]
    synthetic = synthetic_tools()
    alias_inputs = [
        ["srd-lite", "0.3.29", SHA, "cast_spell", "dnd.cast"],
        ["p", "1", "", "with space-and.dots", "c"],
        ["", "", "", "", ""],
        ["pkg-é", "1", SHA, "Bolt ⚡ é!", "cap-ü"],
        ["😀", "2", SHA, "__x__", "c"],
        ["p", "1", SHA, "!!!", "c"],
        ["p", "1", SHA, "y" * 70, "c"],
        ["p", "1", SHA, "a" * 47, "c"],
        ["p", "1", SHA, "a" * 48, "c"],
        ['q"uote\\', "1\n", SHA, "tab\there", "\x00ctl"],
    ]
    aliases = [{"input": item, "alias": session._package_tool_alias(*item)}  # noqa: SLF001
               for item in alias_inputs]
    named = {tool.name: tool for tool in synthetic}
    plain = [tool for tool in synthetic if CONTRACT_KEY not in tool.input_schema
             and tool.name not in {"collides", "properties_not_object"}]
    with_contract = [named["plain"], named["described"], named["cast"]]
    duplicate = make("plain", {"type": "object", "properties": {"other": {}}})
    catalogs = [
        {"name": "srd_and_application", "first_tools": len(srd) + 5, "digest": True,
         **provider_tools(srd + application[:5], digest=True)},
        {"name": "application", "tools": [tool_fields(t) for t in listed], "digest": False,
         **provider_tools(listed, digest=False)},
        {"name": "same_identity", "tools": [tool_fields(t) for t in application[4:6]],
         "digest": False, **provider_tools(application[4:6], digest=False)},
        {"name": "synthetic_plain", "tools": [tool_fields(t) for t in plain], "digest": False,
         **provider_tools(plain, digest=False)},
        {"name": "with_contract", "tools": [tool_fields(t) for t in with_contract],
         "digest": False, **provider_tools(with_contract, digest=False)},
        {"name": "contract_key_null", "tools": [tool_fields(named["contract_null"])],
         "digest": False, **provider_tools([named["contract_null"]], digest=False)},
        {"name": "alias_conflict", "tools": [tool_fields(synthetic[0]), tool_fields(duplicate)],
         "digest": False, **provider_tools([synthetic[0], duplicate], digest=False)},
        {"name": "second_fails", "tools": [tool_fields(synthetic[0]), tool_fields(named["collides"])],
         "digest": False, **provider_tools([synthetic[0], named["collides"]], digest=False)},
        {"name": "empty", "tools": [], "digest": False, **provider_tools([], digest=False)},
    ]
    resolve_catalog = [
        make("roll", {"type": "object"}),
        make("roll", {"type": "object"}, package_id="other.package"),
        make("move", {"type": "object"}),
    ]
    _, resolve_aliases = provider_tools(resolve_catalog, digest=False)["value"].values()
    alias_map = {alias: tool for alias, tool in zip(
        (pair[0] for pair in resolve_aliases), resolve_catalog, strict=True)}
    requests = [resolve_aliases[0][0], resolve_aliases[2][0], "roll", "move", "jump", "",
                "tool_" + resolve_aliases[2][0][5:17]]
    resolve = {
        "catalog": [tool_fields(tool) for tool in resolve_catalog],
        "cases": [{"requested": name, "resolved": (
            None if (found := session._resolve_provider_tool_alias(name, alias_map)) is None
            else [found[0], found[1].name])} for name in requests],  # noqa: SLF001
    }
    document = {
        "aliases": aliases,
        "tools": [tool_case(tool) for tool in srd + application + synthetic],
        "catalogs": catalogs,
        "resolve": resolve,
    }
    text = json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(document["tools"]), "tools,", len(catalogs), "catalogs,", len(text), "bytes")


if __name__ == "__main__":
    main()
