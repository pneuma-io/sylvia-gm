"""Derive which casting inputs each SRD Lite spell can use (a sound over-approximation).

Abstract interpretation over the package's declarative effects, used at package build time by
``scripts.update_srd_argument_contracts``. The selected spell is concrete; every other caller
input is unknown and carries its own name as taint. An input belongs to a spell unless every
place that reads it is provably skipped, or provably refuses a supplied value, for that spell.
Anything the analysis does not model counts as a read, so a contract can only be too wide,
never too narrow: a too-narrow contract would refuse a cast that works today.

Storing a spell's inputs into a pending action is not a use; the later completion runs the
shared ``cast_spell`` resolver, whose uses are added for the casting window.
"""
from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from typing import Any

from sylvia_gm.runtime import _condition_evaluator as engine
from sylvia_gm.runtime._expression_parser import (
    _SANDBOXED_FUNCTIONS,
    BinaryOpNode,
    FieldAccessNode,
    FunctionCallNode,
    LiteralNode,
    UnaryOpNode,
    parse_expression,
)

sys.setrecursionlimit(50000)
EMPTY: frozenset[str] = frozenset()
_FULL_REF = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\}")
_TEMPLATE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\}")


class _Unknown:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


U = _Unknown("U")      # any value, including null
NN = _Unknown("NN")    # any non-null value


class S:
    """A container whose members are abstract values (some unknown or carrying inputs)."""
    __slots__ = ("items",)

    def __init__(self, items: dict[str, A] | list[A]) -> None:
        self.items = items


class A:
    """An abstract value: concrete value, S, U or NN, plus the caller inputs it carries."""
    __slots__ = ("v", "t")

    def __init__(self, v: Any, t: frozenset[str] = EMPTY) -> None:
        self.v, self.t = v, t

    def unknown(self) -> bool:
        return isinstance(self.v, _Unknown)

    def deep(self) -> frozenset[str]:
        if isinstance(self.v, S):
            parts = self.v.items.values() if isinstance(self.v.items, dict) else self.v.items
            return self.t.union(*(p.deep() for p in parts))
        return self.t

    def key(self) -> str:
        if isinstance(self.v, S):
            if isinstance(self.v.items, dict):
                inner = ",".join(f"{k}:{v.key()}" for k, v in sorted(self.v.items.items()))
                body = "{" + inner + "}"
            else:
                body = "[" + ",".join(v.key() for v in self.v.items) + "]"
        elif self.unknown():
            body = repr(self.v)
        else:
            try:
                body = json.dumps(self.v, sort_keys=True, default=str)
            except (TypeError, ValueError):
                body = repr(self.v)
        return body + "|" + ",".join(sorted(self.t))


def unknown(t: frozenset[str] = EMPTY) -> A:
    return A(U, t)


def make(value: Any, t: frozenset[str] = EMPTY) -> A:
    """Wrap a value, keeping a container structured when any member is abstract."""
    return A(value, t)


def lower(a: A) -> A:
    """A concrete value or U, with every input it carries."""
    if not isinstance(a.v, S):
        return a
    if isinstance(a.v.items, dict):
        parts = {k: lower(v) for k, v in a.v.items.items()}
        taint = a.t.union(*(p.t for p in parts.values()))
        if any(p.unknown() for p in parts.values()):
            return unknown(taint)
        return A({k: p.v for k, p in parts.items()}, taint)
    items = [lower(v) for v in a.v.items]
    taint = a.t.union(*(p.t for p in items))
    if any(p.unknown() for p in items):
        return unknown(taint)
    return A([p.v for p in items], taint)


def struct(parts: dict[str, A] | list[A]) -> A:
    values = parts.values() if isinstance(parts, dict) else parts
    if all(not isinstance(p.v, (S, _Unknown)) and not p.t for p in values):
        if isinstance(parts, dict):
            return A({k: p.v for k, p in parts.items()})
        return A([p.v for p in parts])
    return A(S(parts))


def join_value(x: A | None, y: A | None) -> A:
    if x is None or y is None:
        z = x or y
        assert z is not None
        return unknown(z.deep())
    if x.key() == y.key():
        return x
    if isinstance(x.v, S) and isinstance(y.v, S):
        if isinstance(x.v.items, dict) and isinstance(y.v.items, dict):
            names = set(x.v.items) | set(y.v.items)
            return A(S({n: join_value(x.v.items.get(n), y.v.items.get(n)) for n in names}),
                     x.t | y.t)
        if (isinstance(x.v.items, list) and isinstance(y.v.items, list)
                and len(x.v.items) == len(y.v.items)):
            pairs = zip(x.v.items, y.v.items, strict=True)
            return A(S([join_value(a, b) for a, b in pairs]), x.t | y.t)
    return unknown(x.deep() | y.deep())


_EXPR_CACHE: dict[str, Any] = {}


def parsed(expr: str) -> Any:
    if expr not in _EXPR_CACHE:
        try:
            _EXPR_CACHE[expr] = parse_expression(expr)
        except Exception:
            _EXPR_CACHE[expr] = None
    return _EXPR_CACHE[expr]


class Env:
    __slots__ = ("params", "vars")

    def __init__(self, params: dict[str, A], variables: dict[str, A]) -> None:
        self.params, self.vars = params, variables

    def copy(self) -> Env:
        return Env(self.params, dict(self.vars))


def join(a: Env | None, b: Env | None) -> Env | None:
    if a is None:
        return b
    if b is None:
        return a
    out = {}
    for name in set(a.vars) | set(b.vars):
        x, y = a.vars.get(name), b.vars.get(name)
        out[name] = x if x is y else join_value(x, y)
    return Env(a.params, out)


class Analysis:
    def __init__(self, data: dict[str, Any], *, stash_paths: tuple[str, ...] = ()) -> None:
        self.tools = data["tools"]
        self.decls = data.get("parameter_declarations", {})
        self.definitions = data.get("definitions", {})
        self.uses: set[str] = set()
        self.memo: dict[str, tuple[Env | None, frozenset[str]]] = {}
        self.stack: list[str] = []
        self.substitute: tuple[str, A] | None = None
        #: State-write paths that only store inputs for a later resolution, not use them.
        self.stash_paths = stash_paths
        self.dynamic: set[str] = set()

    # -- expressions -------------------------------------------------------------------------
    def ev(self, node: Any, env: Env) -> A:
        if isinstance(node, LiteralNode):
            return A(node.value)
        if isinstance(node, FieldAccessNode):
            return self.field(node, env)
        if isinstance(node, UnaryOpNode):
            x = lower(self.ev(node.operand, env))
            if x.unknown():
                return unknown(x.t)
            if node.operator == "not":
                return A(not engine._to_bool(x.v), x.t)
            if isinstance(x.v, (int, float)) and not isinstance(x.v, bool):
                return A(-x.v, x.t)
            return unknown(x.t)
        if isinstance(node, BinaryOpNode):
            return self.binary(node, env)
        if isinstance(node, FunctionCallNode):
            args = [lower(self.ev(arg, env)) for arg in node.args]
            taint = EMPTY.union(*(a.t for a in args))
            if all(not a.unknown() for a in args) and node.name in _SANDBOXED_FUNCTIONS:
                try:
                    return A(_SANDBOXED_FUNCTIONS[node.name](*[deepcopy(a.v) for a in args]),
                             taint)
                except Exception:
                    return unknown(taint)
            return unknown(taint)
        return unknown()

    def root(self, name: str, env: Env) -> A:
        if self.substitute is not None and self.substitute[0] == name:
            return self.substitute[1]
        if name in env.params:
            if name in env.vars:
                return env.vars[name]
            return env.params[name]
        if name in env.vars:
            return env.vars[name]
        if name == "definitions":
            return A(self.definitions)
        return unknown()

    @staticmethod
    def member(value: A, name: Any) -> A:
        """One member of *value*, as the engine's traversal or index would read it."""
        if isinstance(value.v, S):
            items = value.v.items
            if isinstance(items, dict):
                got = items.get(name) if isinstance(name, str) else None
                if got is None:
                    return A(None, value.t) if isinstance(name, str) else unknown(value.deep())
                return A(got.v, got.t | value.t)
            try:
                got = items[int(name)]
                return A(got.v, got.t | value.t)
            except (ValueError, IndexError, TypeError):
                return unknown(value.deep())
        if value.unknown() or isinstance(value.v, str):
            # A text value may be an entity ID, which the engine reads as that entity.
            return unknown(value.t)
        try:
            return A(engine._traverse_field(value.v, name), value.t)
        except Exception:
            return unknown(value.t)

    def field(self, node: FieldAccessNode, env: Env) -> A:
        value = unknown()
        for index, (name, index_expr) in enumerate(node.segments):
            value = self.root(name, env) if index == 0 else self.member(value, name)
            if index_expr is None:
                continue
            key = lower(self.ev(index_expr, env))
            if key.unknown():
                return unknown(value.deep() | key.t)
            if isinstance(value.v, S):
                if isinstance(value.v.items, dict) and key.v not in value.v.items:
                    return unknown(value.deep() | key.t)  # the engine raises
                got = self.member(value, key.v)
                value = A(got.v, got.t | key.t)
                continue
            if value.unknown():
                return unknown(value.t | key.t)
            container, taint = value.v, value.t | key.t
            try:
                if isinstance(container, dict):
                    if key.v not in container:
                        return unknown(taint)
                    value = A(container[key.v], taint)
                elif isinstance(container, (list, tuple, str)):
                    value = A(container[int(key.v)], taint)
                else:
                    return unknown(taint)
            except Exception:
                return unknown(taint)
        return value

    def truth(self, a: A) -> bool | None:
        if isinstance(a.v, S):
            return len(a.v.items) > 0
        if a.unknown():
            return None
        return engine._to_bool(a.v)

    def binary(self, node: BinaryOpNode, env: Env) -> A:
        op = node.operator
        if op in ("and", "or"):
            left = self.ev(node.left, env)
            lt = self.truth(left)
            if lt is not None:
                if op == "and" and not lt:
                    return A(False, left.deep())
                if op == "or" and lt:
                    return A(True, left.deep())
                right = self.ev(node.right, env)
                rt = self.truth(right)
                if rt is not None:
                    return A(rt, left.deep() | right.deep())
                return unknown(left.deep() | right.deep())
            right = self.ev(node.right, env)
            rt = self.truth(right)
            # Unknown left: a decisive right decides it, or the engine raised.
            if op == "and" and rt is False:
                return A(False, left.deep() | right.deep())
            if op == "or" and rt is True:
                return A(True, left.deep() | right.deep())
            return unknown(left.deep() | right.deep())
        left, right = self.ev(node.left, env), self.ev(node.right, env)
        if op in ("==", "!=") and (
                (left.v is NN and not right.unknown() and right.v is None)
                or (right.v is NN and not left.unknown() and left.v is None)):
            return A(op == "!=", left.t | right.t)
        if op in ("in", "not_in") and isinstance(right.v, S) and isinstance(right.v.items, dict):
            key = lower(left)
            if not key.unknown():
                present = key.v in right.v.items
                return A(present if op == "in" else not present, key.t | right.t)
        left, right = lower(left), lower(right)
        taint = left.t | right.t
        if left.unknown() or right.unknown():
            return unknown(taint)
        try:
            if op in ("==", "!=", ">=", "<=", ">", "<"):
                return A(engine._eval_comparison(op, left.v, right.v), taint)
            if op == "in":
                return A(engine._eval_membership(left.v, right.v), taint)
            if op == "not_in":
                return A(not engine._eval_membership(left.v, right.v), taint)
            if op == "contains":
                return A(engine._eval_contains(left.v, right.v), taint)
            fake = BinaryOpNode(op, LiteralNode(left.v), LiteralNode(right.v))
            return A(engine._eval_binary(fake, None), taint)  # type: ignore[arg-type]
        except Exception:
            return unknown(taint)

    def expr(self, text: Any, env: Env) -> A:
        """Evaluate one declared value the way the effect executor resolves it."""
        if isinstance(text, dict):
            return struct({str(k): self.expr(v, env) for k, v in text.items()})
        if isinstance(text, list):
            return struct([self.expr(v, env) for v in text])
        if not isinstance(text, str):
            return A(deepcopy(text))
        if text in ("true", "false", "null"):
            return A({"true": True, "false": False, "null": None}[text])
        node = parsed(text)
        if node is None:
            return A(text, self.template_taint(text, env))
        value = self.ev(node, env)
        if not value.unknown() and not isinstance(value.v, S) and value.v is None:
            return unknown(value.t)  # literal fallback in the engine; keep it open
        return value

    def condition(self, text: Any, env: Env) -> A:
        node = parsed(str(text))
        if node is None:
            return unknown(self.template_taint(str(text), env))
        value = self.ev(node, env)
        truth = self.truth(value)
        return A(truth, value.deep()) if truth is not None else unknown(value.deep())

    def template_taint(self, text: str, env: Env) -> frozenset[str]:
        taint = EMPTY
        for match in _TEMPLATE.finditer(text):
            node = FieldAccessNode([(part, None) for part in match.group(1).split(".")])
            taint |= self.ev(node, env).deep()
        return taint

    def field_name(self, field: str, env: Env) -> str:
        """Resolve a templated set target whose placeholders are known text."""
        def replace(match: re.Match[str]) -> str:
            node = FieldAccessNode([(part, None) for part in match.group(1).split(".")])
            value = lower(self.ev(node, env))
            if value.unknown() or value.t or not isinstance(value.v, (str, int)) \
                    or isinstance(value.v, bool):
                return match.group(0)
            return str(value.v)
        return _TEMPLATE.sub(replace, field) if "{" in field else field

    def strings_taint(self, body: Any, env: Env) -> frozenset[str]:
        """Every input any string inside *body* might read, as expression or template."""
        if isinstance(body, dict):
            return EMPTY.union(*(self.strings_taint(v, env) for v in body.values()))
        if isinstance(body, list):
            return EMPTY.union(*(self.strings_taint(v, env) for v in body))
        if isinstance(body, str):
            taint = self.template_taint(body, env)
            node = parsed(body)
            if node is not None:
                taint |= self.ev(node, env).deep()
            return taint
        return EMPTY

    def supplied(self, text: Any, env: Env, name: str) -> A:
        """The condition when input *name* is supplied (non-null)."""
        self.substitute = (name, A(NN, frozenset({name})))
        try:
            return self.condition(text, env)
        finally:
            self.substitute = None

    # -- effects ------------------------------------------------------------------------------
    def run(self, effects: Any, env: Env | None) -> Env | None:
        for effect in effects or ():
            if env is None:
                return None
            env = self.effect(effect, env)
        return env

    def mark(self, taint: frozenset[str]) -> None:
        self.uses |= taint

    def effect(self, effect: Any, env: Env) -> Env | None:
        if not isinstance(effect, dict):
            self.mark(self.strings_taint(effect, env))
            return env
        gate = effect.get("condition")
        if isinstance(gate, str) and ("on_true" in effect or "on_false" in effect):
            return self.branch(gate, effect.get("on_true", []), effect.get("on_false", []), env)
        if isinstance(gate, str):
            g = self.condition(gate, env)
            if g.v is False:
                return env
            body = {k: v for k, v in effect.items() if k != "condition"}
            done = self.handler(body, env.copy())
            if g.v is True:
                return done
            for name in g.t:
                passing = self.supplied(gate, env, name)
                if passing.v is True and done is None:
                    continue  # a supplied value only reaches a refusal
                self.uses.add(name)  # including when supplying it skips a default
            return join(done, env)
        return self.handler(effect, env)

    def branch(self, gate: str, on_true: Any, on_false: Any, env: Env) -> Env | None:
        g = self.condition(gate, env)
        if g.v is True or g.v is False:
            return self.run(on_true if g.v else on_false, env.copy())
        yes, no = self.run(on_true, env.copy()), self.run(on_false, env.copy())
        if self.stash_only(on_true) and self.stash_only(on_false):
            return join(yes, no)  # choosing how to store an input is not using it
        for name in g.t:
            passing = self.supplied(gate, env, name)
            if passing.v is True and yes is None or passing.v is False and no is None:
                continue  # a supplied value only reaches a refusal
            self.uses.add(name)
        return join(yes, no)

    def stash_only(self, effects: Any) -> bool:
        return all(isinstance(e, dict) and set(e) == {"set"} and isinstance(e["set"], dict)
                   and any(re.search(p, str(e["set"].get("field", "")))
                           for p in self.stash_paths)
                   for e in effects or ())

    def handler(self, effect: dict[str, Any], env: Env) -> Env | None:
        if "require" in effect:
            body = effect["require"]
            cond = body.get("condition") if isinstance(body, dict) else None
            r = self.condition(cond, env)
            if r.v is True:
                return env
            if r.v is False:
                return None
            for name in r.t:
                if self.supplied(cond, env, name).v is not False:
                    self.uses.add(name)  # a supplied value can pass, and this read decides
            return env
        if "set" in effect:
            body = effect["set"]
            if not isinstance(body, dict):
                return env
            field = self.field_name(str(body.get("field", "")), env)
            value = self.expr(body.get("value"), env) if body.get("value") is not None else A(None)
            if field.startswith("_") and "{" not in field:
                env.vars[field] = value
                return env
            ftaint = self.template_taint(field, env)
            if not any(re.search(p, field) for p in self.stash_paths):
                self.mark(ftaint | value.deep())
            if field.startswith("_"):
                env.vars[field.split(".")[0]] = unknown(ftaint | value.deep())
            elif field.startswith("{"):
                # An unresolved name may replace any context variable: forget them all.
                self.dynamic.add(field)
                spread = ftaint | value.deep()
                for name, held in list(env.vars.items()):
                    env.vars[name] = unknown(held.deep() | spread)
            return env
        if "call" in effect:
            return self.call(effect, env)
        if "for_each" in effect:
            return self.for_each(effect["for_each"], env)
        if "table_lookup" in effect and isinstance(effect["table_lookup"], dict):
            body = effect["table_lookup"]
            store = effect.get("store_as", "_table_result")
            key = lower(self.expr(str(body.get("key", "")), env))
            self.mark(key.t)
            table: Any = self.definitions
            for part in str(body.get("table", "")).split(".")[1:]:
                table = table.get(part) if isinstance(table, dict) else None
            if not isinstance(table, dict) or key.unknown():
                env.vars[str(store)] = unknown(key.t)
                return env
            lookup = int(key.v) if isinstance(key.v, float) and key.v == int(key.v) else key.v
            try:
                value = table.get(lookup)
            except TypeError:
                value = None
            if value is None and body.get("required"):
                return None
            if value is None:
                value = body.get("default")
            env.vars[str(store)] = A(deepcopy(value), key.t)
            return env
        if "list_push" in effect:
            body = effect["list_push"]
            if isinstance(body, dict):
                ref = str(body.get("list", ""))
                value = self.expr(body.get("value"), env)
                prior = env.vars.get(ref, A([]))
                target = str(effect.get("store_as", ref))
                if isinstance(prior.v, S) and isinstance(prior.v.items, list):
                    env.vars[target] = A(S([*prior.v.items, value]), prior.t)
                elif isinstance(prior.v, list):
                    env.vars[target] = A(S([*(A(x) for x in prior.v), value]), prior.t)
                else:
                    env.vars[target] = unknown(prior.deep() | value.deep())
            return env
        # Any other handler consumes every input it names and may set its result names.
        name = next((k for k in effect if k not in ("store_as", "description")), None)
        body = effect.get(name) if name else None
        taint = self.strings_taint(body, env)
        self.mark(taint)
        for key in ("store_as", "as", "into"):
            for holder in (effect, body if isinstance(body, dict) else {}):
                if isinstance(holder.get(key), str):
                    env.vars[holder[key]] = unknown(taint)
        if name in ("list_pop", "sort") and isinstance(body, dict):
            for key in ("list", "collection"):
                if isinstance(body.get(key), str):
                    env.vars[body[key]] = unknown(taint)
        return env

    def for_each(self, body: Any, env: Env) -> Env | None:
        if not isinstance(body, dict):
            return env
        ref = body.get("collection", "")
        if not isinstance(ref, str):
            return env
        coll = env.params[ref] if ref in env.params else env.vars.get(ref, A(None))
        var = body.get("as", "_item")
        effects = body.get("do", [])
        items: list[A] | None = None
        if isinstance(coll.v, S) and isinstance(coll.v.items, list):
            items = [A(i.v, i.t | coll.t) for i in coll.v.items]
        elif isinstance(coll.v, list):
            items = [A(i, coll.t) for i in coll.v]
        elif not coll.unknown() and not isinstance(coll.v, S):
            return env  # not a list: the engine skips the loop
        if items is not None and len(items) <= 2000:
            state: Env | None = env
            for item in items:
                if state is None:
                    return None
                state.vars[var] = item
                state = self.run(effects, state)
            return state
        # Unknown length: zero or more iterations, approximated to a fixpoint.
        state = env
        current = env
        for _ in range(4):
            inner = current.copy()
            inner.vars[var] = unknown(coll.deep())
            state = join(state, self.run(effects, inner))
            if state is None:
                return None
            current = state
        return state

    def call(self, effect: dict[str, Any], env: Env) -> Env | None:
        body = effect["call"]
        if isinstance(body, str):
            tool, params = body, effect.get("params", {})
        else:
            tool, params = body.get("call", body.get("name", "")), body.get("params", {})
        bindings: dict[str, A] = {}
        for key, value in (params or {}).items():
            if isinstance(value, str):
                match = _FULL_REF.fullmatch(value)
                if match:
                    node = FieldAccessNode([(part, None) for part in match.group(1).split(".")])
                    bindings[key] = self.ev(node, env)
                else:
                    taint = self.template_taint(value, env)
                    bindings[key] = A(value, taint) if not taint and "{" not in value \
                        else unknown(taint)
            else:
                bindings[key] = A(deepcopy(value))
        result = self.invoke(str(tool), bindings)
        if result is None:
            return None
        store = effect.get("store_as")
        if isinstance(store, str):
            env.vars[store] = unknown(EMPTY.union(*(b.deep() for b in bindings.values())))
        export = effect.get("export_context")
        if export == "*":
            for name, value in result.vars.items():
                if name.startswith("_"):
                    env.vars[name] = value
        elif isinstance(export, dict):
            for destination, source in export.items():
                env.vars[destination] = result.vars.get(source, unknown())
        return env

    def invoke(self, tool: str, bindings: dict[str, A]) -> Env | None:
        definition = self.tools.get(tool)
        everything = EMPTY.union(*(b.deep() for b in bindings.values()))
        if definition is None:
            self.mark(everything)
            return Env({}, {})
        key = tool + "#" + ";".join(f"{k}={v.key()}" for k, v in sorted(bindings.items()))
        if key in self.memo:
            final, uses = self.memo[key]
            self.uses |= uses
            return final
        if key in self.stack or len(self.stack) > 80:
            self.mark(everything)
            return Env({}, {})
        self.stack.append(key)
        outer = self.uses
        self.uses = set()
        try:
            env: Env | None = Env(self.tool_params(definition, bindings), {})
            for pre in definition.get("pre_conditions", ()):
                if env is None:
                    break
                cond = pre if isinstance(pre, str) else pre.get("condition", "")
                env = self.handler({"require": {"condition": cond}}, env)
            env = self.run(definition.get("effects", []), env)
            uses = frozenset(self.uses)
        finally:
            self.stack.pop()
            inner = self.uses
            self.uses = outer | inner
        self.memo[key] = (env, uses)
        return env

    def tool_params(self, definition: dict[str, Any], bindings: dict[str, A]) -> dict[str, A]:
        params: dict[str, A] = {}
        for entry in definition.get("parameters", ()):
            decl = self.decls.get(entry, {}) if isinstance(entry, str) else entry
            name = decl.get("name")
            if not name:
                continue
            if name in bindings:  # the engine applies a default only to an absent input
                params[name] = bindings[name]
            elif "default" in decl:
                params[name] = A(deepcopy(decl["default"]))
            else:
                params[name] = A(None)
        for name, value in bindings.items():
            params.setdefault(name, value)
        return params

    def parameter_names(self, tool: str) -> list[str]:
        names = []
        for entry in self.tools[tool].get("parameters", ()):
            decl = self.decls.get(entry, {}) if isinstance(entry, str) else entry
            names.append(decl["name"])
        return names

    def top(self, tool: str, spell: str) -> tuple[frozenset[str], bool]:
        """Inputs *spell* can reach through *tool*, and whether any execution succeeds."""
        bindings = {name: A(spell, frozenset({name})) if name == "spell"
                    else unknown(frozenset({name})) for name in self.parameter_names(tool)}
        self.uses = set()
        final = self.invoke(tool, bindings)
        return frozenset(self.uses), final is not None


STASH_PATHS = (r"pending_srd_actions\.\{[^}]+\}\.parameters",)
#: Each model-facing casting tool and the tools whose uses together make its contract.
CASTING_TOOLS = {
    "begin_srd_spell_action": ("begin_srd_spell_action", "cast_spell"),
    "cast_spell_outside_combat": ("cast_spell_outside_combat",),
}


def spell_inputs(runtime: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """For each casting tool, the inputs each declared spell can use."""
    analysis = Analysis(runtime, stash_paths=STASH_PATHS)
    result: dict[str, dict[str, list[str]]] = {}
    for tool, sources in CASTING_TOOLS.items():
        declared = set(analysis.parameter_names(tool))
        spells = next(decl.get("enum_values", ()) for decl in (
            analysis.decls.get(e, {}) if isinstance(e, str) else e
            for e in analysis.tools[tool]["parameters"]) if decl.get("name") == "spell")
        result[tool] = {}
        for spell in spells:
            uses: set[str] = set()
            for source in sources:
                uses |= analysis.top(source, spell)[0]
            result[tool][spell] = sorted(uses & declared)
    if analysis.dynamic:
        print(json.dumps({"unresolved_variable_writes": sorted(analysis.dynamic)}), flush=True)
    return result
