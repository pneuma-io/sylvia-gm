"""Trace caller recipient inputs through the retained casting dispatch bindings.

This is build-time analysis only. An opaque or scene-derived value is left to its
existing runtime owner; it never creates a new caller requirement.
"""

import operator
from contextlib import contextmanager
from functools import lru_cache

from sylvia_gm.runtime._exceptions import ExpressionError
from sylvia_gm.runtime._expression_parser import (
    BinaryOpNode,
    FieldAccessNode,
    FunctionCallNode,
    LiteralNode,
    UnaryOpNode,
    parse_expression,
)

UNKNOWN = object()
ENGINE, EMPTY, ABSENT = "engine", "empty", "absent"
INPUTS = {"target", "targets", "object_target_id"}
OPERATORS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "//": operator.floordiv,
    "%": operator.mod,
    "in": lambda left, right: left in right,
    "not in": lambda left, right: left not in right,
}


def _expression(value):
    return value[1:-1] if value.startswith("{") and value.endswith("}") else value


@lru_cache(maxsize=4096)
def _parse(value):
    return parse_expression(_expression(value))


class _Scope:
    """Trace the supported binding expressions; unknown runtime facts stay unknown.

    Each read sees only earlier assignments. Branch guards keep their entry point,
    and a callee reads its arguments at the caller's dispatch point.
    """

    def __init__(self, runtime, name, known, incoming=None, ancestors=()):
        self.runtime, self.name = runtime, name
        self.tool, self.base = runtime["tools"][name], known
        self.incoming, self.ancestors = incoming or {}, (*ancestors, name)
        self.writes, self.calls, self.exports, self.requires = {}, [], [], []
        self._known_busy, self._sources_busy = set(), set()
        self._children, self._known_cache = {}, {}
        self.point = 0
        self._collect(self.tool.get("effects", []), ())
        self.point += 1

    @contextmanager
    def at(self, point):
        """Resolve an expression against the assignments available at that point."""
        previous = self.point
        self.point = point
        try:
            yield
        finally:
            self.point = previous

    def _write(self, name, value, guards, source=None):
        self.writes.setdefault(name, []).append((value, guards, source, self.point))

    def _collect(self, rows, guards):
        for row in rows:
            self.point += 1
            condition = row.get("condition")
            active = (*guards, (condition, self.point)) if condition else guards
            if "require" in row:
                self.requires.append((row["require"]["condition"], active, self.point))
            if "set" in row:
                self._write(row["set"]["field"], row["set"].get("value"), active)
            if "list_push" in row:
                self._write(row["list_push"]["list"], row["list_push"]["value"], active, "push")
            if "call" in row:
                self.calls.append((row, active, self.point))
                exports = row.get("export_context", {})
                if exports == "*":
                    self.exports.append((row, active, self.point))
                    exports = {}
                for destination, source in exports.items():
                    self._write(destination, source, active, row)
            if "table_lookup" in row and "store_as" in row:
                lookup = row["table_lookup"]
                self._write(row["store_as"], lookup["table"] + "[" + lookup["key"] + "]", active)
            if "spatial_query" in row:
                destination = row["spatial_query"].get("store_as", row.get("store_as"))
                if destination:
                    self._write(destination, None, active, ENGINE)
            if "for_each" in row:
                loop = row["for_each"]
                self._write(loop["as"], loop.get("collection", loop.get("list")), active)
                collection = loop.get("collection", loop.get("list"))
                loop_guards = (
                    (*active, ("len(" + collection + ") > 0", self.point))
                    if isinstance(collection, str)
                    else active
                )
                self._collect(loop["do"], loop_guards)
            if "on_true" in row:
                self._collect(row["on_true"], active)
            if "on_false" in row:
                self._collect(
                    row["on_false"], (*guards, ("not (" + condition + ")", active[-1][1]))
                )

    def guard_value(self, guard):
        expression, point = guard
        with self.at(point):
            return self.value(expression)

    def feasible(self, guards):
        return not any(self.guard_value(guard) is False for guard in guards)

    def child(self, call):
        identity = id(call)
        if identity not in self._children:
            self._children[identity] = _Scope(
                self.runtime,
                call["call"],
                self.base,
                {key: (self, value, self.point) for key, value in call.get("params", {}).items()},
                self.ancestors,
            )
        return self._children[identity]

    def known(self, name):
        if name in self.base:
            return self.base[name]
        identity = (name, self.point)
        if identity in self._known_cache:
            return self._known_cache[identity]
        if identity in self._known_busy:
            return UNKNOWN
        self._known_busy.add(identity)
        try:
            values = []
            if name in self.incoming:
                parent, value, point = self.incoming[name]
                with parent.at(point):
                    values.append(parent.value(value))
            for value, guards, source, point in self.producers(name):
                if not self.feasible(guards):
                    continue
                if isinstance(source, dict) and source["call"] not in self.ancestors:
                    with self.at(point):
                        result = self.child(source).known(value)
                elif source is None:
                    with self.at(point):
                        result = self.value(value)
                else:
                    result = UNKNOWN
                if all(self.guard_value(guard) is True for guard in guards):
                    values = [result]
                else:
                    values.append(result)
            if values and all(value is not UNKNOWN and value == values[0] for value in values):
                result = values[0]
            else:
                result = UNKNOWN
            self._known_cache[identity] = result
            return result
        finally:
            self._known_busy.remove(identity)

    def producers(self, name):
        producers = [row for row in self.writes.get(name, ()) if row[3] < self.point]
        if name.startswith("_"):
            for call, guards, point in self.exports:
                if point >= self.point:
                    continue
                if call["call"] not in self.ancestors and self.feasible(guards):
                    with self.at(point):
                        child = self.child(call)
                    if name in child.writes:
                        producers.append((name, guards, call, point))
        yield from sorted(producers, key=lambda row: row[3])

    def value(self, value):
        if isinstance(value, dict):
            return {key: self.value(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self.value(child) for child in value]
        if not isinstance(value, str):
            return value
        try:
            return self._value(_parse(value))
        except (ExpressionError, TypeError, ValueError, KeyError, IndexError, AttributeError):
            return UNKNOWN

    def _value(self, node):
        if isinstance(node, LiteralNode):
            return node.value
        if isinstance(node, FieldAccessNode):
            value = self.known(node.segments[0][0])
            for index, (name, subscript) in enumerate(node.segments):
                if value is UNKNOWN:
                    return UNKNOWN
                if index:
                    value = value.get(name, UNKNOWN) if isinstance(value, dict) else UNKNOWN
                if subscript is not None:
                    key = self._value(subscript)
                    value = UNKNOWN if key is UNKNOWN else value[key]
            return value
        if isinstance(node, UnaryOpNode):
            value = self._value(node.operand)
            return (
                UNKNOWN if value is UNKNOWN else (not value if node.operator == "not" else -value)
            )
        if isinstance(node, BinaryOpNode):
            left = self._value(node.left)
            if node.operator == "and" and left is False:
                return False
            if node.operator == "or" and left is True:
                return True
            right = self._value(node.right)
            if node.operator == "and":
                return (
                    False
                    if left is False or right is False
                    else UNKNOWN
                    if UNKNOWN in (left, right)
                    else bool(left and right)
                )
            if node.operator == "or":
                return (
                    True
                    if left is True or right is True
                    else UNKNOWN
                    if UNKNOWN in (left, right)
                    else bool(left or right)
                )
            if left is UNKNOWN or right is UNKNOWN or node.operator not in OPERATORS:
                return UNKNOWN
            return OPERATORS[node.operator](left, right)
        if isinstance(node, FunctionCallNode) and node.name == "len":
            value = self._value(node.args[0])
            return UNKNOWN if value is UNKNOWN else len(value)
        return UNKNOWN

    def sources(self, name):
        identity = (name, self.point)
        if identity in self._sources_busy:
            return set()
        self._sources_busy.add(identity)
        try:
            found = set()
            if name in self.incoming:
                parent, value, point = self.incoming[name]
                with parent.at(point):
                    found.update(parent.origins(value))
            elif name in INPUTS:
                found.add(name)
            elif name.startswith("_") and name.endswith("_id"):
                parameter = name[1:-3]
                if parameter in self.incoming or parameter in self.parameter_names():
                    found.update(self.sources(parameter))
            for value, guards, source, point in self.producers(name):
                if not self.feasible(guards):
                    continue
                if isinstance(source, dict) and source["call"] not in self.ancestors:
                    with self.at(point):
                        sources = self.child(source).sources(value)
                elif source is None or source == "push":
                    with self.at(point):
                        sources = self.origins(value)
                else:
                    sources = {ENGINE}
                if source != "push" and all(self.guard_value(guard) is True for guard in guards):
                    found = set(sources)
                else:
                    found.update(sources)
            return found or {ENGINE}
        finally:
            self._sources_busy.remove(identity)

    def parameter_names(self):
        declarations = self.runtime.get("parameter_declarations", {})
        return {
            declarations[p]["name"] if isinstance(p, str) else p["name"]
            for p in self.tool.get("parameters", [])
        }

    def origins(self, value):
        if value is None:
            return {ABSENT}
        if isinstance(value, list):
            return set().union(*(self.origins(child) for child in value)) if value else {EMPTY}
        if not isinstance(value, str):
            return {ENGINE}
        try:
            node = _parse(value)
        except (ExpressionError, ValueError):
            return {ENGINE}
        if isinstance(node, FieldAccessNode):
            return self.sources(node.segments[0][0])
        return {ABSENT} if isinstance(node, LiteralNode) and node.value in (None, "") else {ENGINE}

    def condition(self, value):
        """Retain public route selectors; defer guards needing private computed facts."""
        try:
            return self._condition(_parse(value))
        except (ExpressionError, TypeError, ValueError, KeyError, IndexError):
            return None

    def _condition(self, node):
        value = self._value(node)
        if value is True or value is False:
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, (str, int, float)):
            return repr(value)
        if isinstance(node, FieldAccessNode):
            root = node.segments[0][0]
            if root not in self.parameter_names() or self.name != "cast_spell":
                return None
            result = root
            for index, (field, subscript) in enumerate(node.segments):
                if index:
                    result += "." + field
                if subscript is not None:
                    rendered = self._condition(subscript)
                    if rendered is None:
                        return None
                    result += "[" + rendered + "]"
            return result
        if isinstance(node, UnaryOpNode):
            operand = self._condition(node.operand)
            return None if operand is None else node.operator + " (" + operand + ")"
        if isinstance(node, BinaryOpNode):
            left, right = self._condition(node.left), self._condition(node.right)
            if node.operator == "and":
                if left == "false" or right == "false":
                    return "false"
                if left == "true":
                    return right
                if right == "true":
                    return left
            if node.operator == "or":
                if left == "true" or right == "true":
                    return "true"
                if left == "false":
                    return right
                if right == "false":
                    return left
            if left is not None and right is not None:
                return "(" + left + " " + node.operator + " " + right + ")"
        return None

    def family_calls(self, names, reachable, routes=()):
        for call, guards, point in self.calls:
            if call["call"] not in reachable:
                continue
            if not self.feasible(guards):
                continue
            if call["call"] in names:
                yield self, call, point, (*routes, (self, guards))
            elif call["call"] not in self.ancestors:
                with self.at(point):
                    child = self.child(call)
                yield from child.family_calls(names, reachable, (*routes, (self, guards)))


def _reachable(runtime, names):
    graph = {}

    def calls(value):
        if isinstance(value, dict):
            if "call" in value:
                yield value["call"]
            for child in value.values():
                yield from calls(child)
        elif isinstance(value, list):
            for child in value:
                yield from calls(child)

    for name, tool in runtime["tools"].items():
        graph[name] = set(calls(tool.get("effects", [])))
    reachable = set(names)
    while True:
        previous = len(reachable)
        reachable.update(name for name, children in graph.items() if children & reachable)
        if len(reachable) == previous:
            return reachable


def recipient_requirements(runtime, spell, in_combat):
    """Return only caller requirements supported by actual feasible family bindings."""
    profile = runtime["definitions"]["spells"][spell]
    family = profile["family"]
    names = {
        name for name in ("cast_" + family, "cast_spell_" + family) if name in runtime["tools"]
    }
    known = {
        "spell": spell,
        "definitions": runtime["definitions"],
        "combat": {"encounter_active": in_combat},
        "_ms": spell,
        "_spell_definition": profile,
        "_spell_cast_failed": False,
        "_pending_spell_operation_lost": False,
    }
    scope = _Scope(runtime, "cast_spell", known)
    requirements = set()
    for caller, call, point, routes in scope.family_calls(names, _reachable(runtime, names)):
        with caller.at(point):
            family_scope = caller.child(call)
        tool = runtime["tools"][call["call"]]
        declarations = runtime.get("parameter_declarations", {})
        for raw in tool.get("parameters", []):
            parameter = declarations[raw] if isinstance(raw, str) else raw
            field = parameter["name"]
            if field not in {"target", "targets"} or not parameter.get("required", True):
                continue
            with caller.at(point):
                origins = caller.origins(call.get("params", {}).get(field))
            if ENGINE in origins:
                continue
            strict = field == "target"
            if field == "targets":
                checks = [(check["condition"], (), 0) for check in tool.get("pre_conditions", [])]
                checks.extend(family_scope.requires)
                failures = []
                for values in ([], ["'recipient'"]):
                    probe = _Scope(
                        runtime,
                        call["call"],
                        known,
                        {**family_scope.incoming, field: (caller, values, point)},
                        caller.ancestors,
                    )
                    refused = set()
                    for index, (condition, guards, check_point) in enumerate(checks):
                        with probe.at(check_point):
                            if probe.feasible(guards) and probe.value(condition) is False:
                                refused.add(index)
                    failures.append(refused)
                strict = parameter.get("min_items", 0) > 0 or bool(failures[0] - failures[1])
            if EMPTY in origins and not strict:
                continue
            inputs = tuple(sorted(origins & INPUTS))
            if inputs:
                guards = []
                for owner, path in routes:
                    for guard, guard_point in path:
                        with owner.at(guard_point):
                            guards.append(owner.condition(guard))
                if None in guards:
                    continue
                when = (
                    " and ".join(sorted({guard for guard in guards if guard != "true"})) or "true"
                )
                requirements.add((field, inputs, strict, when))
    return sorted(requirements)


def recipient_contracts(runtime):
    """Group equivalent caller checks without naming any particular spell in code."""
    groups = {}
    for spell in sorted(runtime["definitions"]["srd_lite_implemented"]):
        for combat in (False, True):
            for field, inputs, strict, when in recipient_requirements(runtime, spell, combat):
                groups.setdefault((combat, field, inputs, strict, when), set()).add(spell)
    result = {}
    aliases = runtime["definitions"].get("srd_lite_aliases", {})
    for index, ((combat, field, inputs, strict, when), spells) in enumerate(sorted(groups.items())):
        spells.update(alias for alias, canonical in aliases.items() if canonical in spells)
        result["r" + str(index)] = {
            "in_combat": combat,
            "field": field,
            "caller_inputs": list(inputs),
            "nonempty": strict,
            "when": when,
            "spells": sorted(spells),
        }
    return result


def condition_inputs(expression):
    """List public selector roots needed to evaluate one retained route guard."""

    def visit(node):
        if isinstance(node, FieldAccessNode):
            return {node.segments[0][0]}
        if isinstance(node, UnaryOpNode):
            return visit(node.operand)
        if isinstance(node, BinaryOpNode):
            return visit(node.left) | visit(node.right)
        if isinstance(node, FunctionCallNode):
            return set().union(*(visit(argument) for argument in node.args))
        return set()

    return visit(_parse(expression))
