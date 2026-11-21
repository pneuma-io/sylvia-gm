"""Generate evaluation vectors for sylvia_engine::expr from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_expression_eval_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in, so the Rust tests need no Python.
Reference tooling for the port, retired with the Python source.

One rich execution context (parameters that name entities, context variables of every value
type the runtime stores, dice results, entities, combat state, ruleset config and definitions)
and a generated corpus: every operator over a grid of operands, unary operators, field paths
and indexes, and every sandboxed function over sampled arguments. Each case records Python's
result or exception, and a `refuse` mark where an integer beyond 64 bits or a complex number
appeared on the way, which Rust declares instead (`differences.md`).

Values are written tagged: `{"$float": repr}`, `{"$tuple": [...]}`, `{"$object": class,
"fields": {...}}`, `{"$method": name, "owner": value}`; ints, strings, booleans, null, lists
and dicts as themselves.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import sys
from pathlib import Path

import sylvia_gm.runtime._condition_evaluator as ce
from sylvia_gm import dice
from sylvia_gm.runtime._exceptions import ExpressionError
from sylvia_gm.runtime._types import ExecutionBudget, ExecutionContext

OUTPUT = Path(__file__).with_name("expression_eval_vectors.json")
MAX_OPERATIONS = 400


class Unencodable(Exception):
    pass


def encode(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return {"$float": repr(value)}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, tuple):
        return {"$tuple": [encode(item) for item in value]}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"$object": type(value).__name__,
                "fields": {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if callable(value) and hasattr(value, "__self__") and hasattr(value, "__name__"):
        return {"$method": value.__name__, "owner": encode(value.__self__)}
    raise Unencodable(type(value).__name__)


def context() -> tuple[dict, ExecutionContext]:
    roll = dice.RollResult(20, 2, (17, 4), 17, 3)
    check = dice.CheckResult(True, dice.RollResult(20, 1, (12,), 12, 5), 15, 2)
    pool = dice.PoolResult(10, 4, (8, 3, 6, 1), 6, 2, 2, 1, 0)
    hunger = dice.HungerPoolResult((8, 4), (10, 1), 2, 2, 1, 0, 6, 0)
    params = {
        "actor": "pc-1", "target": "wolf", "count": 3, "label": "Sword", "flag": True, "items": [1, 2, 3],
        "shadow": 5, "missing_entity": "nobody", "nothing": None, "rate": 0.25,
    }
    variables = {
        "_n": 7, "_f": 2.5, "_neg": -3, "_zero": 0, "_fzero": 0.0, "_nzero": -0.0, "_s": "abc", "_e": "",
        "_u": "Régles ☃", "_l": [1, 2.0, "a", None, True, [1, 2], {"k": 1}, (3, 4)], "_t": (1, "b", 2.5),
        "_one": (9,), "_empty": [], "_d": {"a": 1, "b": [1, 2], "c": {"d": "x"}, "": 0},
        "_roll": roll, "_check": check, "_pool": pool, "_hunger": hunger,
        "_max": 2**63 - 1, "_min": -(2**63), "_big2": 2**62, "_inf": math.inf, "_ninf": -math.inf,
        "_nan": math.nan, "_bigf": 1e300, "_half": 0.5, "_third": 1 / 3, "_ints": [3, 1, 2, 1],
        "_strs": ["b", "a", "c"], "_mixed": [1, "a"], "_nested": [[1, 2], [1, 1], [0, 5, 1]],
        "_tuples": [(2, "a"), (1, "b"), (2, "A")], "_floats": [0.1] * 10 + [1e16, 1.0, -1e16],
        "_long": list(range(80, 0, -1)), "_hand": [1, 1, 1, 2, 3, 4, 5, 5],
        "_pats": [[1, 1, 1], [2, 3, 4], [5, 5], [1, 2, 3], [3, 4, 5]], "_sizes": [3, 3, 2],
        "_universe": [1, 2, 3, 4, 5, 6], "_strhand": ["m1", "m1", "p2"], "_strpats": [["m1", "m1"], ["p2"]],
        "shadow": 9,
        # Lists the corpus passes to functions (the expression language has no list literals).
        "_ones": [1, 1], "_five": [5], "_two": [2], "_pair12": [[1, 2]], "_maxpair": [2**63 - 1, 1],
        "_maxneg": [2**63 - 1, -1], "_mix25": [1, 2.5], "_infs": [math.inf, -math.inf],
        "_nanlist": [1, math.nan, 0], "_bools": [True, 0, 2.5], "_lists": [[1], [1, 0], [0, 9]],
        "_nullone": [None, 1], "_twoone": [2, 1], "_onepats": [[1], [1, 1]], "_threes": [3, 3],
    }
    entities = {
        "pc-1": {"name": "Ada", "hp": 12, "stats": {"str": 14, "dex": 10}, "tags": ["hero"], "kind": "pc"},
        "wolf": {"name": "Wolf", "hp": 0, "stats": {"str": 12}, "kind": "npc"},
    }
    combat = {"round": 2, "order": ["pc-1", "wolf"], "turn_index": 0}
    ruleset = {"features": {"crits": True, "dc": 10}, "label": "Lite"}
    definitions = {"table": {"key": "value", "n": 3}, "tiles": [1, 2, 3], "phases": ["draw", "discard"]}
    ctx = ExecutionContext(params=params, vars=variables, entities=entities, combat_state=combat,
                           ruleset_config=ruleset, definitions=definitions)
    encoded = {
        "params": encode(params), "vars": encode(variables), "entities": encode(entities),
        "combat_state": encode(combat), "ruleset_config": encode(ruleset), "definitions": encode(definitions),
        "max_operations": MAX_OPERATIONS,
    }
    return encoded, ctx


ATOMS = [
    "null", "true", "false", "0", "1", "-1", "7", "2.5", "0.0", "'a'", "''", "'abc'", "99999999999999999999",
    "_n", "_f", "_neg", "_zero", "_fzero", "_nzero", "_s", "_e", "_u", "_l", "_t", "_one", "_empty", "_d",
    "_roll", "_check", "_pool", "_hunger", "_max", "_min", "_big2", "_inf", "_ninf", "_nan", "_bigf",
    "_half", "_third", "_ints", "_strs", "_nested", "_tuples",
    "actor", "target", "count", "label", "flag", "items", "shadow", "missing_entity", "nothing", "rate",
    "combat", "ruleset", "definitions", "combatants", "party_members", "unknown_name",
]
OPERAND_GRID = [
    "null", "true", "0", "1", "-1", "7", "2.5", "0.0", "'a'", "'abc'", "_n", "_f", "_neg", "_zero",
    "_fzero", "_nzero", "_s", "_l", "_t", "_one", "_empty", "_d", "_roll", "_max", "_min", "_big2",
    "_inf", "_ninf", "_nan", "_bigf", "_half", "_third", "_ints", "_strs", "_nested", "_tuples",
    "count", "flag", "rate", "actor",
]
# An integer base other than 0, 1 and -1 raised to one of these never finishes in Python.
HUGE_EXPONENTS = {"_max", "_big2"}
INT_BASES = {"7", "_n", "_neg", "_max", "_min", "_big2", "count"}
BINARY = ["+", "-", "*", "//", "%", "**", "==", "!=", ">=", "<=", ">", "<", "in", "not in", "contains", "and", "or"]
PATHS = [
    "_roll.total", "_roll.results", "_roll.to_dict", "_roll.nothing", "_check.roll_result.total",
    "_check.success", "_pool.successes", "_hunger.hunger_results", "_s.upper", "_s.nothing", "_n.real",
    "_n.imag", "_n.numerator", "_n.denominator", "_f.real", "_f.imag", "_f.is_integer", "flag.real",
    "flag.numerator", "_d.a", "_d.b", "_d.c.d", "_d.zz", "_d.c.d.e", "_l.count", "_t.index", "_d.keys",
    "actor.name", "actor.stats.str", "actor.stats.wis", "target.hp", "missing_entity.name",
    "combat.round", "combat.order", "ruleset.features.crits", "definitions.table.key", "definitions.nope.x",
    "nothing.x", "count.real", "label.lower", "unknown_name.a.b",
    "_l[0]", "_l[-1]", "_l[7]", "_l[8]", "_l[-9]", "_l[5][1]", "_l[6].k", "_l[6]['k']", "_t[1]", "_t[-1]",
    "_one[0]", "_empty[0]", "_s[0]", "_s[-1]", "_s[3]", "_u[6]", "_d['a']", "_d['zz']", "_d[0]", "_d['']",
    "_l['x']", "_l[1.9]", "_l[-1.5]", "_l[_inf]", "_l[_nan]", "_l[_bigf]", "_l[true]", "_l[null]",
    "_l[_l]", "_d[true]", "_d[1.5]", "_d[null]", "_d[_l]", "_s[_f]", "_s['a']", "_n[0]", "_roll[0]",
    "nothing[0]", "_l[_n]", "items[_neg]", "_roll.results[0]", "_roll.results[5]", "_d.b[1]", "_d.b[0][0]",
    "_nested[2][1]", "actor.tags[0]", "combat.order[count]", "_l[0][1]", "_d['c'].d", "ruleset['label']",
    "definitions.tiles[-1]", "_t[_half]", "_s[_half]", "_l[_max]", "_l[_min]",
]
FUNCTIONS = ["max", "min", "abs", "floor", "ceil", "len", "merge", "indices", "portion", "joined",
             "replaced", "without", "occurrences", "text", "total", "one", "ordered", "partitions", "completions"]
FUNCTION_ARGS = OPERAND_GRID + ["_floats", "_long", "_hand", "_pats", "_sizes", "_universe", "_strhand",
                                "_strpats", "1025", "1024", "-2", "3", "_d", "_check"]
FIXED_CALLS = [
    "max()", "min()", "abs()", "abs(1, 2)", "floor()", "ceil(1, 2)", "len()", "len(1, 2)", "merge()",
    "merge(_d)", "merge(_d, _d, _d)", "indices()", "portion(_l)", "partitions()", "partitions(_hand)",
    "partitions(_hand, _pats, _sizes, _n)", "completions(_hand, _pats, _sizes)",
    "completions(_hand, _pats, _sizes, _universe, 1)", "text()", "one(1, 2)",
    "partitions(_hand, _pats, _sizes)", "completions(portion(_hand, 0, 7), _pats, _sizes, _universe)",
    "partitions(_strhand, _strpats, _twoone)", "partitions(_ones, _onepats, _ones)",
    "partitions(portion(_hand, 0, 6), _pats, _threes)", "partitions(_hand, _pats, _threes)",
    "portion(_ints, -3, -1)", "portion(_ints, 2, 1)", "portion(_ints, -10, 10)", "portion(_ints, 1.0, 2)",
    "portion(_ints, true, 2)", "joined(_ints, _strs)", "joined(_long, _long)", "replaced(_ints, 0, 'x')",
    "replaced(_ints, 4, 1)", "replaced(_ints, -1, 1)", "without(_ints, _ones)", "without(_ints, _five)",
    "without(_l, _two)", "without(_l, _pair12)", "occurrences(_l, 1)", "occurrences(_ints, 1.0)",
    "occurrences(_l, true)", "text(_u)", "text(_inf)", "text(1.0)", "total(_floats)", "total(_l)",
    "total(_maxpair)", "total(_maxneg)", "total(_mix25)", "total(_infs)", "ordered(_long)",
    "ordered(_tuples)", "ordered(_nested)", "ordered(_mixed)", "ordered(_nanlist)", "ordered(_strs)",
    "ordered(_bools)", "ordered(_lists)", "max(_nested)", "min(_tuples)",
    "max(1, 2.0, 2)", "max(2.0, 2, 1)", "min(_nan, 1)", "min(1, _nan)", "max(_empty)", "max(_d)", "min(_t)",
    "max(_mixed)", "max(_nullone)", "max(_roll)", "abs(_min)", "abs(_neg)", "abs(_nzero)", "floor(_bigf)",
    "floor(_inf)", "ceil(_nan)", "floor(-2.5)", "ceil(-2.5)", "floor(_roll)", "len(_u)", "len(_d)",
    "len(_roll)", "len(null)", "merge(_d, {})", "indices(0)", "indices(1024)", "indices(1025)",
    "indices(true)", "indices(2.0)",
]


def expressions() -> list[str]:
    out: list[str] = []
    out += ATOMS + PATHS
    for atom in ATOMS:
        out += [f"-{atom}", f"not {atom}", f"- {atom}"]
    for left in OPERAND_GRID:
        for right in OPERAND_GRID:
            for op in BINARY:
                if op == "**" and right in HUGE_EXPONENTS and left in INT_BASES:
                    continue  # Python computes the exact power for ever; Rust refuses it (row 5).
                out.append(f"{left} {op} {right}")
    rng = random.Random(7700)
    for name in FUNCTIONS:
        for _ in range(160):
            args = ", ".join(rng.choice(FUNCTION_ARGS) for _ in range(rng.choice((1, 1, 1, 2, 2, 3, 4))))
            out.append(f"{name}({args})")
    out += FIXED_CALLS
    out += ["'" + "a" * 200 + "' + '" + "b" * 200 + "'", "_s + _s", "_l + _l", "_t + _t", "_l + _t",
            "_t + _l", "_l * 3", "3 * _t", "_l * -1", "_l * 2.0", "2.5 * _l", "_l * _l", "_d * 2",
            "_roll + 1", "_l - _l", "_d + _d", "(-8) ** (1 / 3)", "_neg ** 0.5", "2 ** 62", "2 ** 63",
            "2 ** -1", "0 ** -1", "0.0 ** -1", "10.0 ** 400", "_nan ** 0", "1 ** _nan", "_inf ** -1",
            "_max // -1", "_min // -1", "_min % -1", "_bigf // 1", "_inf // 1", "_nan // 1", "7 // 2.0",
            "-7 // 2", "-7 % 2", "7 % -2", "-7.5 % 2", "7.5 // -2", "0.0 // -3", "1 // _inf",
            "_max + 1 - 1", "_min - 1", "-_min", "_max * 2", "_big2 * 2", "_big2 * -2",
            "not _l", "not _empty", "not _roll", "not _s.upper", "_s.upper == _s.upper", "_roll.to_dict",
            "_d['a'] + _d.b[1]", "len(_l) > 2 and _l[0] == 1", "1 < 2 < 3", "a.b + c"]
    return list(dict.fromkeys(out))


class Watch:
    """Record whether any node's value was an integer beyond i64 or a complex number."""

    def __init__(self) -> None:
        self.flag: str | None = None
        self.original = ce.evaluate

    def __call__(self, node, ctx):
        value = self.original(node, ctx)
        if isinstance(value, complex) and self.flag is None:
            self.flag = "complex"
        elif isinstance(value, int) and not isinstance(value, bool) and not -(2**63) <= value < 2**63:
            self.flag = self.flag or "int_range"
        return value


def main() -> int:
    encoded_context, ctx = context()
    watch = Watch()
    ce.evaluate = watch
    cases = []
    try:
        for text in expressions():
            watch.flag = None
            ctx.budget = ExecutionBudget(max_operations=MAX_OPERATIONS)
            try:
                result = ce.evaluate_expression(text, ctx)
                outcome = {"result": encode(result)}
            except (ExpressionError, ValueError, TypeError, IndexError, OverflowError, ZeroDivisionError,
                    RuntimeError, KeyError, MemoryError) as error:
                outcome = {"error": str(error), "class": type(error).__name__}
            except Unencodable as reason:
                outcome = {"unencodable": str(reason)}
            if watch.flag:
                outcome = {"refuse": watch.flag, "python": outcome}
            message = json.dumps(outcome)
            if "object at 0x" in message:
                outcome = {"nondeterministic": True}
            cases.append({"text": text, **outcome})
    finally:
        ce.evaluate = watch.original
    document = {"python": sys.version.split()[0], "context": encoded_context, "cases": cases}
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True) + "\n").encode("ascii"))
    kinds = {}
    for case in cases:
        kind = next(k for k in ("result", "error", "refuse", "nondeterministic", "unencodable") if k in case)
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"{len(cases)} cases {kinds} -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
