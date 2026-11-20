"""Sylvia's own tool loader: one generic mechanic tool and compact appended definitions.

On a route whose provider has no way to add tools mid-conversation (``SYLVIA_GM_TOOL_LIST=
deferred`` outside ``NATIVE_DEFERRED_ROUTES`` and ``PROVIDER_DEFERRED_ROUTES``), the declared
tool list holds the application's own tools and ``call_mechanic`` only. It never changes, so
the cached prefix never breaks, and the model attends to a handful of tool names.

- A Ruleset mechanic is defined once, when the turn loop first offers it, by an appended
  ``SYLVIA_GM_TOOL_DEFINITIONS`` record in a terse signature form (not JSON Schema).
- A ``SYLVIA_GM_CALLABLE_TOOLS`` record names the mechanics the engine accepts now; PR 140's
  current argument choices follow in their own record. Each is appended when it changes.
- The model calls a mechanic through ``call_mechanic``. The turn loop unwraps the call before
  admission (``unwrap_mechanic_call``), so canonical history, tool evidence, admission and the
  engine's validation see the mechanic itself, exactly as for a natively declared tool.
- The ledger renders canonical history back in the form the model used
  (``project_conversation``), deterministically, so a restart sends the same bytes.

Mechanics are shown by a short name: the operation part of the provider alias
(``tool_<digest>_<operation>``), unless two catalog mechanics share it.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MECHANIC_TOOL_NAME = "call_mechanic"
DEFINITIONS_LABEL = "SYLVIA_GM_TOOL_DEFINITIONS"
CALLABLE_LABEL = "SYLVIA_GM_CALLABLE_TOOLS"
CHOICES_LABEL = "SYLVIA_GM_ARGUMENT_CHOICES"
MECHANIC_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MECHANIC_TOOL_NAME,
        "description": (
            f"Use one game mechanic. name: a mechanic named callable in the latest "
            f"{CALLABLE_LABEL} record. arguments: an object of the shape its "
            f"{DEFINITIONS_LABEL} entry shows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The mechanic's exact name."},
                "arguments": {"type": "object", "description": "The mechanic's arguments."},
            },
            "required": ["name", "arguments"],
        },
    },
}
_ALIAS = re.compile(r"^tool_[0-9a-f]{12}_(.+)$")
_CONSTRAINTS = (("minimum", ">="), ("maximum", "<="), ("exclusiveMinimum", ">"),
                ("exclusiveMaximum", "<"), ("minLength", "min length"),
                ("maxLength", "max length"), ("minItems", "min items"),
                ("maxItems", "max items"), ("pattern", "pattern"), ("default", "default"))


def _function(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = tool.get("function")
    return raw if isinstance(raw, Mapping) else tool


def tool_name(tool: Mapping[str, Any]) -> str:
    return str(_function(tool).get("name", ""))


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _words(text: Any) -> str:
    return " ".join(str(text or "").split())


# ── Short names ───────────────────────────────────────────────────────────────────────────


def _operation(alias: str) -> str:
    match = _ALIAS.match(alias)
    return match.group(1) if match else alias


class MechanicNames:
    """Short display names for a fixed catalog's mechanics, and back to provider aliases."""

    def __init__(self, catalog: Iterable[str], reserved: Iterable[str]) -> None:
        self._reserved = {*reserved, MECHANIC_TOOL_NAME}
        counts: dict[str, int] = {}
        aliases = sorted(set(catalog))
        for alias in aliases:
            counts[_operation(alias)] = counts.get(_operation(alias), 0) + 1
        self._catalog_short = {
            alias: (_operation(alias) if counts[_operation(alias)] == 1
                    and _operation(alias) not in self._reserved else alias)
            for alias in aliases
        }
        self._taken = set(self._catalog_short.values())

    def short(self, alias: str) -> str:
        """The name the model sees; a tool outside the catalog keeps a unique short form."""
        known = self._catalog_short.get(alias)
        if known is not None:
            return known
        if alias in self._reserved:
            return alias
        operation = _operation(alias)
        return (operation if operation not in self._taken and operation not in self._reserved
                else alias)

    def alias(self, name: str, offered: Iterable[str]) -> str:
        """The offered provider alias a model's name means, else the name unchanged."""
        offered = list(offered)
        if name in offered:
            return name
        matches = [alias for alias in offered if self.short(alias) == name]
        return matches[0] if len(matches) == 1 else name


# ── Compact definitions ───────────────────────────────────────────────────────────────────


def _note(schema: Mapping[str, Any]) -> str:
    """Constraints a type expression cannot show, as a short trailing comment."""
    parts = [f"{label} {_literal(schema[key]) if key in {'pattern', 'default'} else schema[key]}"
             for key, label in _CONSTRAINTS if key in schema]
    if schema.get("uniqueItems") is True:
        parts.append("unique")
    reference = schema.get("x-sylvia-gm-reference")
    if isinstance(reference, str):
        parts.append(f"a {reference} id")
    if schema.get("x-sylvia-gm-castable-spells"):
        parts.append("a castable spell")
    aliases = schema.get("x-sylvia-gm-value-aliases")
    if isinstance(aliases, Mapping) and aliases:
        parts.append("aliases " + ", ".join(f"{k}={v}" for k, v in sorted(aliases.items())))
    return "; ".join(parts)


class _Renderer:
    def __init__(self, root: Mapping[str, Any]) -> None:
        defs = root.get("$defs")
        self.defs: Mapping[str, Any] = defs if isinstance(defs, Mapping) else {}

    def resolve(self, schema: Any) -> Mapping[str, Any]:
        seen: set[str] = set()
        while isinstance(schema, Mapping) and isinstance(schema.get("$ref"), str):
            name = schema["$ref"].rsplit("/", 1)[-1]
            target = self.defs.get(name)
            if not isinstance(target, Mapping) or name in seen:
                break
            seen.add(name)
            schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
        return schema if isinstance(schema, Mapping) else {}

    def type_of(self, raw: Any, depth: int) -> str:
        schema = self.resolve(raw)
        if "const" in schema:
            return _literal(schema["const"])
        if isinstance(schema.get("enum"), list):
            return " | ".join(_literal(value) for value in schema["enum"]) or "never"
        for key in ("oneOf", "anyOf"):
            if isinstance(schema.get(key), list):
                return " | ".join(self.type_of(option, depth) for option in schema[key])
        kind = schema.get("type")
        if isinstance(kind, list):
            return " | ".join(self.type_of({**schema, "type": item}, depth) for item in kind)
        if kind == "array" or "items" in schema:
            inner = self.type_of(schema.get("items", {}), depth)
            return f"({inner})[]" if " | " in inner else f"{inner}[]"
        if kind == "object" or "properties" in schema:
            return self.object(schema, depth)
        return str(kind) if isinstance(kind, str) else "any"

    def object(self, schema: Mapping[str, Any], depth: int) -> str:
        properties = schema.get("properties")
        extra = schema.get("additionalProperties")
        if not isinstance(properties, Mapping) or not properties:
            if isinstance(extra, Mapping):
                return "{[key: string]: " + self.type_of(extra, depth + 1) + "}"
            return "{}" if extra is False else "object"
        required = set(schema.get("required") or ())
        pad = " " * (depth + 1)
        lines = ["{"]
        for name, raw in properties.items():
            child = self.resolve(raw)
            description = _words(child.get("description"))
            if description:
                lines.append(f"{pad}// {description}")
            note = _note(child)
            optional = "" if name in required else "?"
            lines.append(f"{pad}{name}{optional}: {self.type_of(child, depth + 1)}"
                         + (f" // {note}" if note else ""))
        if isinstance(extra, Mapping):
            lines.append(f"{pad}[key: string]: {self.type_of(extra, depth + 1)}")
        lines.append(" " * depth + "}")
        return "\n".join(lines)


def compact_definition(tool: Mapping[str, Any], name: str | None = None) -> str:
    """One mechanic: its name, description and argument shape in a TypeScript-like form."""
    function = _function(tool)
    parameters = function.get("parameters")
    schema = parameters if isinstance(parameters, Mapping) else {"type": "object"}
    description = _words(function.get("description"))
    return (f"## {name or function.get('name', '')}\n"
            + (f"{description}\n" if description else "")
            + "arguments: " + _Renderer(schema).object(schema, 0))


#: A mechanic whose compact definition is longer than this (characters) is listed by name and
#: summary only; its first call returns the definition instead of running. Large definitions
#: (a spell action's hundreds of optional fields) would otherwise sit in every later request.
INLINE_LIMIT = 700
#: The summary of a mechanic listed without its arguments: its description, cut to this.
SUMMARY_LIMIT = 240
LISTED_NOTE = "(arguments given on first use)"


def _summary(tool: Mapping[str, Any]) -> str:
    description = _words(_function(tool).get("description"))
    if len(description) <= SUMMARY_LIMIT:
        return description
    cut = description[:SUMMARY_LIMIT].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + " …"


def definitions_record(
    tools: Sequence[Mapping[str, Any]], names: MechanicNames,
) -> tuple[str, list[str]]:
    """The appended definitions of mechanics first offered at this step, and which of them
    are defined in full (the rest are listed; their first call returns the definition)."""
    full: list[str] = []
    listed: list[str] = []
    defined: list[str] = []
    for tool in tools:
        short = names.short(tool_name(tool))
        text = compact_definition(tool, short)
        if len(text) <= INLINE_LIMIT:
            full.append(text)
            defined.append(tool_name(tool))
        else:
            listed.append(f"## {short} {LISTED_NOTE}\n{_summary(tool)}")
    content = (f"{DEFINITIONS_LABEL} (application control, not player speech): game mechanics, "
               f"each used by calling {MECHANIC_TOOL_NAME} with its name and an arguments "
               "object of the shape shown (? marks an optional field). For a mechanic marked "
               f"{LISTED_NOTE}, your first call returns its arguments and runs nothing; then "
               "call it again. Each stays defined for the rest of the session; the latest "
               f"{CALLABLE_LABEL} record says which can be used now.\n\n"
               + "\n\n".join([*full, *listed]))
    return content, defined


DEFINITION_CODE = "mechanic_definition"


def definition_result(tool: Mapping[str, Any], short: str) -> dict[str, Any]:
    """The result of a first call to a listed mechanic: its definition; nothing ran."""
    return {
        "status": "definition_given", "code": DEFINITION_CODE, "state_changed": False,
        "mechanic": short, "definition": compact_definition(tool, short),
        "instruction": (f"Nothing ran. Call {MECHANIC_TOOL_NAME} again for {short} with "
                        "arguments of this shape, if it still fits."),
    }


def defined_in_result(message: Mapping[str, Any]) -> str | None:
    """The short name a tool result defined (``definition_result``), else None."""
    content = message.get("content")
    if message.get("role") != "tool" or not isinstance(content, str) or (
            DEFINITION_CODE not in content):
        return None
    try:
        value = json.loads(content)
    except ValueError:
        return None
    mechanic = value.get("mechanic") if isinstance(value, Mapping) else None
    return (mechanic if value.get("code") == DEFINITION_CODE and isinstance(mechanic, str)
            else None)


# ── What the engine accepts now ───────────────────────────────────────────────────────────


def callable_record(
    declared: Sequence[Mapping[str, Any]], offered: Sequence[Mapping[str, Any]] | None,
    names: MechanicNames,
) -> str:
    """Name the mechanics (and say which declared tools) the engine accepts at this step."""
    listed = {tool_name(tool) for tool in declared} - {MECHANIC_TOOL_NAME}
    allowed = {tool_name(tool) for tool in offered or ()}
    mechanics = sorted(names.short(name) for name in allowed - listed)
    refused = sorted(listed - allowed)
    head = (f"{CALLABLE_LABEL} (application control, not player speech; the latest record "
            "with this label applies): ")
    if not mechanics and not (listed & allowed):
        return head + ("no tool can be called at this step. Reply without a tool call; a tool "
                       "call now is refused.")
    text = head + (f"mechanics you can use now with {MECHANIC_TOOL_NAME}: "
                   + ", ".join(mechanics) + "." if mechanics
                   else f"no mechanic can be used now; do not call {MECHANIC_TOOL_NAME}.")
    if refused:
        text += " These listed tools cannot be called now: " + ", ".join(refused) + "."
    return text + " Any other call is refused."


def choices_record(
    fixed: Sequence[Mapping[str, Any]], offered: Sequence[Mapping[str, Any]] | None,
    names: MechanicNames,
) -> str:
    """PR 140's current argument choices, keyed by the names the model sees."""
    from ._fixed_tool_projection import argument_choices_record

    renamed = []
    for tool in offered or ():
        function = dict(_function(tool))
        renamed.append({"type": "function", "function": {
            **function, "name": names.short(str(function.get("name", "")))}})
    catalog = [{"type": "function", "function": {
        **dict(_function(tool)), "name": names.short(tool_name(tool))}} for tool in fixed]
    return CHOICES_LABEL + " (application control, not player speech):" + (
        argument_choices_record(catalog, renamed))


# ── The call itself ───────────────────────────────────────────────────────────────────────


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def unwrap_mechanic_call(arguments: str) -> tuple[str, str] | None:
    """The mechanic name and its arguments (JSON) inside a ``call_mechanic`` call, or None.

    Arguments given as a JSON string are accepted; a missing arguments field is an empty
    object. Anything else (no name, arguments that are not an object) is None.
    """
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return None
    if not isinstance(value, Mapping):
        return None
    name, inner = value.get("name"), value.get("arguments", {})
    if isinstance(inner, str):
        try:
            inner = json.loads(inner or "{}")
        except ValueError:
            return None
    if not isinstance(name, str) or not name.strip() or not isinstance(inner, Mapping):
        return None
    return name.strip(), _compact_json(inner)


def wrap_mechanic_arguments(name: str, arguments: str) -> str:
    """The ``call_mechanic`` arguments for a canonical mechanic call, byte-stable."""
    try:
        inner = json.loads(arguments or "{}")
    except ValueError:
        inner = arguments
    return _compact_json({"name": name, "arguments": inner})


def project_conversation(
    message: Mapping[str, Any], declared: frozenset[str], names: MechanicNames,
) -> dict[str, Any]:
    """A canonical conversation message as the model sent and sees it on a loader route.

    An assistant call to a mechanic (any name not declared) becomes a ``call_mechanic`` call
    with the mechanic's short name; its result is named after ``call_mechanic`` too.
    """
    projected = copy.deepcopy(dict(message))
    calls = projected.get("tool_calls")
    if projected.get("role") == "assistant" and isinstance(calls, list):
        wrapped = []
        for call in calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            name = function.get("name") if isinstance(function, Mapping) else None
            if isinstance(name, str) and name not in declared:
                call = {**call, "function": {
                    **function, "name": MECHANIC_TOOL_NAME,
                    "arguments": wrap_mechanic_arguments(
                        names.short(name), str(function.get("arguments") or "{}")),
                }}
            wrapped.append(call)
        projected["tool_calls"] = wrapped
    elif (projected.get("role") == "tool" and isinstance(projected.get("name"), str)
          and projected["name"] not in declared):
        projected["name"] = MECHANIC_TOOL_NAME
    return projected
