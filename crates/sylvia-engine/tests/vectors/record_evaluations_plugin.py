"""Pytest plugin for generate_expression_recorded_vectors.py: record expression evaluations.

While the snapshot's own tests run real SRD Lite and Riichi Mahjong mechanics, this records
every top-level `evaluate_expression` call: the text, the value of each root name the
evaluation read, and the result or the exception class and message. So that the recording is
the same on every run, the default dice stream is seeded before each test, and the `shuffle`
effect's `secrets.SystemRandom()` (OS entropy, which deals Riichi's walls) is replaced by a
seeded stream for the recording only. Reference tooling for the
port, retired with the Python source.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import random
import threading
import types

import sylvia_gm.runtime._condition_evaluator as ce
import sylvia_gm.runtime._effect_executor as effects
from sylvia_gm import dice

_state = threading.local()
_out = None
_seen: set[bytes] = set()
_original_parse = ce._evaluation_expression
_original_evaluate = ce.evaluate
_original_root = ce._resolve_root


class Unsupported(Exception):
    pass


def encode(value, depth: int = 0):
    if depth > 60:
        raise Unsupported("depth")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if -(2**63) <= value < 2**63:
            return value
        raise Unsupported("int beyond i64")
    if isinstance(value, float):
        return {"$float": repr(value)}
    if isinstance(value, list):
        return [encode(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return {"$tuple": [encode(item, depth + 1) for item in value]}
    if isinstance(value, dict):
        if not all(isinstance(key, str) and not key.startswith("$") for key in value):
            raise Unsupported("dict key")
        return {key: encode(item, depth + 1) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"$object": type(value).__name__,
                "fields": {f.name: encode(getattr(value, f.name), depth + 1) for f in dataclasses.fields(value)}}
    raise Unsupported(type(value).__name__)


def _parse_spy(expression):
    _state.text = expression
    return _original_parse(expression)


def _root_spy(name, ctx):
    value = _original_root(name, ctx)
    roots = getattr(_state, "roots", None)
    if roots is not None and name not in roots:
        roots[name] = value
    return value


def _evaluate_spy(node, context):
    depth = getattr(_state, "depth", 0)
    if depth:
        _state.depth = depth + 1
        try:
            return _original_evaluate(node, context)
        finally:
            _state.depth -= 1
    text = getattr(_state, "text", None)
    _state.text = None
    _state.roots = {}
    _state.depth = 1
    budget = context.budget
    start = None if budget is None else [budget.max_operations, budget.operations]
    outcome = None
    try:
        result = _original_evaluate(node, context)
        outcome = {"result": result}
        return result
    except Exception as error:  # noqa: BLE001 - recorded, then re-raised
        outcome = {"error": type(error).__name__, "message": str(error)}
        raise
    finally:
        _state.depth = 0
        if text is not None and outcome is not None:
            _write(text, _state.roots, outcome, start)


def _write(text, roots, outcome, budget) -> None:
    try:
        record = {"text": text, "roots": {name: encode(value) for name, value in roots.items()}}
        if "result" in outcome:
            record["result"] = encode(outcome["result"])
        else:
            record["error"] = outcome["error"]
            record["message"] = outcome["message"]
        # Only the pattern search consumes the budget; elsewhere it would just defeat dedupe.
        if budget is not None and ("partitions(" in text or "completions(" in text):
            record["budget"] = budget
    except Unsupported:
        return
    line = json.dumps(record, ensure_ascii=False)
    digest = hashlib.sha256(line.encode("utf-8")).digest()
    if digest in _seen:
        return
    _seen.add(digest)
    _out.write(line + "\n")


def pytest_configure(config):
    global _out
    _out = open(os.environ["SYLVIA_EVAL_RECORD"], "w", encoding="utf-8")
    ce._evaluation_expression = _parse_spy
    ce.evaluate = _evaluate_spy
    ce._resolve_root = _root_spy


def pytest_runtest_setup(item):
    dice.set_seed(20261121)
    shuffle_stream = random.Random(20261121)
    effects.secrets = types.SimpleNamespace(SystemRandom=lambda: shuffle_stream)


def pytest_unconfigure(config):
    if _out is not None:
        _out.close()
