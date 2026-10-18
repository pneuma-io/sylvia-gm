"""Read-only character constraints executed by the existing bounded Ruleset runtime."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._exceptions import EffectExecutionError, PreConditionError
from sylvia_gm.runtime._schema_validator import _validate_effects
from sylvia_gm.runtime._types import ExecutionBudget, ExecutionContext

_LOCAL = re.compile(r"_[A-Za-z0-9_]+\Z")
_HANDLERS = {"set", "list_push", "require", "condition", "for_each", "table_lookup"}


def _reject(code: str, message: str) -> CommandRejected:
    return CommandRejected(message, code=code, diagnostics=(RulesetDiagnostic(
        code=code, phase="character_initial_state", message=message,
        path="initial_state_validation",
        guidance="Correct the indicated character choice or the Ruleset's validation declaration.",
    ),))


def validate_character_rules(program: Mapping[str, object] | None) -> dict[str, Any] | None:
    """Admit only local calculations, table reads and requirements, with no effectful callbacks."""
    if program is None:
        return None
    code = "character_initial_state_validation_invalid"
    if (not isinstance(program, Mapping)
            or set(program) != {"description", "definitions", "effects"}):
        raise _reject(code, "Character validation needs description, definitions and effects.")
    raw = canonical_json_bytes(program)
    if len(raw) > 1024 * 1024:
        raise _reject(code, "Character validation exceeds its declaration size limit.")
    value = json.loads(raw)
    if (not isinstance(value["description"], str) or not 1 <= len(value["description"]) <= 2000
            or not isinstance(value["definitions"], dict)
            or not isinstance(value["effects"], list) or not value["effects"]):
        raise _reject(code, "Character validation declarations are invalid.")
    pending = [(effect, 0) for effect in value["effects"]]
    count = 0
    while pending:
        effect, depth = pending.pop()
        count += 1
        if count > 1024 or depth > 32 or not isinstance(effect, dict):
            raise _reject(code, "Character validation exceeds its effect limit.")
        handlers = set(effect) & _HANDLERS
        if isinstance(effect.get("condition"), str) and len(handlers) > 1:
            handlers.remove("condition")
        if len(handlers) != 1:
            raise _reject(code, "Character validation permits only read-only runtime effects.")
        handler = next(iter(handlers))
        allowed = {handler, "condition"}
        if handler == "condition":
            allowed |= {"on_true", "on_false"}
            if not isinstance(effect[handler], str):
                raise _reject(code, "Character validation uses flat conditional branches.")
            branches = [effect.get("on_true", []), effect.get("on_false", [])]
        else:
            body = effect[handler]
            if not isinstance(body, dict):
                raise _reject(code, "Character validation effect bodies must be objects.")
            branches = []
            local = None
            if handler == "set":
                local = body.get("field")
            elif handler == "list_push":
                local = body.get("list")
            elif handler == "for_each":
                local = body.get("as")
                branches = [body.get("do", [])]
            elif handler == "table_lookup":
                allowed.add("store_as")
                local = effect.get("store_as")
                if not isinstance(body.get("table"), str) or not body["table"].startswith(
                    "definitions.",
                ):
                    raise _reject(code, "Character validation tables must use local definitions.")
            if handler != "require" and (not isinstance(local, str) or not _LOCAL.fullmatch(local)):
                raise _reject(code, "Character validation may write only private local variables.")
        if set(effect) - allowed or any(not isinstance(branch, list) for branch in branches):
            raise _reject(code, "Character validation has unsupported effect fields.")
        for branch in branches:
            pending.extend((nested, depth + 1) for nested in branch)
    if _validate_effects("character_validation", value["effects"]):
        raise _reject(code, "Character validation contains invalid runtime effects.")
    return value


def campaign_character_state(plan: Mapping[str, object], namespace: str) -> dict[str, Any]:
    """Copy only the selected Ruleset's declared starting keys for campaign calculations."""
    result = {str(item["key"]): item["value"]
              for item in plan["initial_state"].get("mechanics", ())
              if item["namespace"] == namespace}
    return json.loads(canonical_json_bytes(result))


def verify_character_rules(
    participant: Mapping[str, object], state: Mapping[str, object],
    program: Mapping[str, object] | None, setting_data: Mapping[str, object] | None = None,
    *, campaign_state: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Check private copies and return local results without mutation or external access."""
    checked = validate_character_rules(program)
    if checked is None:
        return {}
    inputs = json.loads(canonical_json_bytes({
        "participant": participant, "state": state, "setting_data": setting_data or {},
        "campaign_state": campaign_state or {},
    }))
    context = ExecutionContext(params=inputs, definitions=checked["definitions"],
                               budget=ExecutionBudget())
    try:
        execute_effects(checked["effects"], context)
    except PreConditionError as error:
        raise _reject("character_initial_state_rule_rejected", str(error)) from error
    except EffectExecutionError as error:
        raise _reject("character_initial_state_validation_failed",
                      "The Ruleset could not validate the character's declared choices.") from error
    return json.loads(canonical_json_bytes(context.vars))
