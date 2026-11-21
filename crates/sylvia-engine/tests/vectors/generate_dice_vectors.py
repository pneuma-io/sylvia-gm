"""Generate reference vectors for sylvia_engine's dice, dice expressions, RNG queue and lower().

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-engine/tests/vectors/generate_dice_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in, so the Rust tests need no Python.
Reference tooling for the port, retired with the Python source.

Every result is recorded the way it leaves Python: `to_dict()` values, their `json.dumps`
text and their canonical bytes, or the exception class and `str(error)`.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from sylvia_gm import dice
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime._effect_executor import _parse_dice_expr
from sylvia_gm.runtime._initiative import _roll_initiative_expr
from sylvia_gm.runtime._rng_queue import RNGQueue

OUTPUT = Path(__file__).with_name("dice_vectors.json")


def outcome(call) -> dict:
    try:
        value = call()
    except (ValueError, TypeError, IndexError) as error:
        return {"error": type(error).__name__, "message": str(error)}
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return {"ok": payload, "json": json.dumps(payload),
                "canonical": canonical_json_bytes(payload).decode("utf-8")}
    return {"ok": value}


def weight_tables() -> list[list[dict]]:
    tables = [
        [{"name": "a", "weight": 70}, {"name": "b", "weight": 30}],
        [{"name": "a", "weight": 1}, {"name": "b", "weight": 1}, {"name": "c", "weight": 1}],
        [{"name": "a", "weight": 0}, {"name": "b", "weight": 0}],
        [{"name": "a"}, {"name": "b"}],
        [{"name": "a", "weight": 5}, {"name": "b"}],
        [{"name": "a", "weight": True}, {"name": "b", "weight": 2}],
        [{"name": "a", "weight": 2.5}, {"name": "b", "weight": 1}],
        [{"name": "a", "weight": 0.1} for _ in range(10)],
        [{"name": "a", "weight": 0.0}, {"name": "b", "weight": -0.0}],
        [{"name": "a", "weight": -5}, {"name": "b", "weight": 2}],
        [{"name": "a", "weight": -1}, {"name": "b", "weight": 1}],
        [{"name": "a", "weight": "3"}, {"name": "b", "weight": 1}],
        [{"name": "a", "weight": 1}, {"name": "b", "weight": None}],
        [{"name": "a", "weight": 1.5}, {"name": "b", "weight": [1]}],
        [{"name": "a", "weight": 1}, {"name": "b", "weight": {"x": 1}}],
        [{"name": "a", "weight": 1e16}, {"name": "b", "weight": 1.0}, {"name": "c", "weight": -1e16}],
        [{"name": "a", "weight": 1}, {"name": "b", "weight": 0.5}, {"name": "c", "weight": 3}],
        [{"name": "a", "weight": 2**40}, {"name": "b", "weight": 2**40}],
        [{"name": "a", "weight": 3, "w2": 9}, {"name": "b", "weight": 1, "w2": 1}],
        [],
    ]
    rows = random.Random(77)
    for _ in range(6):
        tables.append([{"name": f"r{i}", "weight": rows.randint(0, 50)} for i in range(rows.randint(1, 12))])
    return tables


def call_script() -> list[dict]:
    script: list[dict] = []
    for sides in (2, 4, 6, 20, 100, 1000, 1, 0, -3, 1001):
        for count in (1, 3, 1000, 0, -1, 1001):
            script.append({"fn": "roll", "sides": sides, "count": count, "modifier": 2})
    for sides in (4, 6, 8, 10, 12, 20, 100):
        for modifier in (0, 5, -3):
            script.append({"fn": "die", "sides": sides, "modifier": modifier})
    for n, sides in ((3, 6), (2, 10), (0, 6), (4, 1)):
        script.append({"fn": "ndn", "n": n, "sides": sides, "modifier": 1})
    for modifier in (0, 4, -2):
        script += [{"fn": "advantage", "modifier": modifier}, {"fn": "disadvantage", "modifier": modifier}]
    for dc in (1, 10, 15, 25):
        for flags in ((False, False), (True, False), (False, True), (True, True)):
            script.append({"fn": "check", "dc": dc, "modifier": 3, "advantage": flags[0], "disadvantage": flags[1]})
    for sides, count in ((6, 2), (6, 5), (2, 3), (10, 1)):
        for explode_on in (None, 1, 2, sides, sides + 1, 0):
            for max_explosions in (100, 0, 3, -1):
                script.append({"fn": "explode", "sides": sides, "count": count, "modifier": 0,
                               "explode_on": explode_on, "max_explosions": max_explosions})
    script.append({"fn": "explode", "sides": 1, "count": 1, "modifier": 0, "explode_on": None, "max_explosions": 1})
    for sides, count in ((10, 5), (6, 8), (10, 1)):
        for threshold in (1, 6, sides, sides + 1, sides + 2, 0):
            for modifier in (0, 2, -10):
                script.append({"fn": "count_successes", "sides": sides, "count": count,
                               "threshold": threshold, "modifier": modifier})
    previous = [
        {"sides": 20, "count": 2, "results": [1, 17], "total": 18, "modifier": 0},
        {"sides": 6, "count": 4, "results": [1, 6, 3, 1], "total": 11, "modifier": 1},
        {"sides": 10, "count": 3, "results": [9, 2, 6], "total": 17, "modifier": 0},
        {"sides": 0, "count": 1, "results": [1], "total": 1, "modifier": 0},
        {"sides": 1, "count": 2, "results": [1, 1], "total": 2, "modifier": 0},
        {"sides": 6, "count": 0, "results": [], "total": 0, "modifier": 0},
    ]
    for prior in previous:
        for predicate in ("all", "ones", "failures", "bogus", "ALL"):
            for threshold in (None, 5):
                script.append({"fn": "reroll", "previous": prior, "predicate": predicate,
                               "modifier": 2, "threshold": threshold})
    for table in weight_tables():
        for roll_total in (None, 1, 30, 70, 71, 100, 0, -5):
            script.append({"fn": "weighted_pick", "items": table, "field": "weight", "roll_total": roll_total})
        script.append({"fn": "weighted_pick", "items": table, "field": "w2", "roll_total": None})
    for pool, hunger in ((6, 2), (8, 0), (5, 5), (1, 1), (0, 0), (3, -1), (3, 4), (12, 3)):
        for sides, threshold in ((10, 6), (10, 1), (6, 4), (1, 6), (10, 0)):
            script.append({"fn": "hunger_roll", "pool": pool, "hunger": hunger, "sides": sides,
                           "threshold": threshold, "modifier": 1})
    random.Random(20261121).shuffle(script)
    script += [{"fn": "die", "sides": 20, "modifier": 0}] * 300
    script += [{"fn": "hunger_roll", "pool": 10, "hunger": 4, "sides": 10, "threshold": 6, "modifier": 0}] * 40
    return script


def run(call: dict) -> dict:
    fn = call["fn"]
    if fn == "roll":
        return outcome(lambda: dice.roll(call["sides"], call["count"], call["modifier"]))
    if fn == "die":
        helper = {4: dice.d4, 6: dice.d6, 8: dice.d8, 10: dice.d10, 12: dice.d12, 20: dice.d20, 100: dice.d100}
        return outcome(lambda: helper[call["sides"]](call["modifier"]))
    if fn == "ndn":
        return outcome(lambda: dice.ndn(call["n"], call["sides"], call["modifier"]))
    if fn == "advantage":
        return outcome(lambda: dice.advantage(call["modifier"]))
    if fn == "disadvantage":
        return outcome(lambda: dice.disadvantage(call["modifier"]))
    if fn == "check":
        return outcome(lambda: dice.check(call["dc"], call["modifier"], call["advantage"], call["disadvantage"]))
    if fn == "explode":
        return outcome(lambda: dice.explode(call["sides"], call["count"], call["modifier"],
                                            call["explode_on"], call["max_explosions"]))
    if fn == "count_successes":
        return outcome(lambda: dice.count_successes(call["sides"], call["count"], call["threshold"], call["modifier"]))
    if fn == "reroll":
        prior = call["previous"]
        previous = dice.RollResult(prior["sides"], prior["count"], tuple(prior["results"]), prior["total"], prior["modifier"])
        return outcome(lambda: dice.reroll(previous, call["predicate"], call["modifier"], call["threshold"]))
    if fn == "weighted_pick":
        items = [dict(item) for item in call["items"]]
        roll_result = None
        if call["roll_total"] is not None:
            roll_result = dice.RollResult(100, 1, (call["roll_total"],), call["roll_total"], 0)

        def pick():
            picked = dice.weighted_pick(items, call["field"], roll_result)
            return None if picked is None else next(i for i, item in enumerate(items) if item is picked)
        return outcome(pick)
    if fn == "hunger_roll":
        return outcome(lambda: dice.hunger_roll(call["pool"], call["hunger"], call["sides"],
                                                call["threshold"], call["modifier"]))
    raise AssertionError(fn)


I64_MAX, I64_MIN = 2**63 - 1, -(2**63)


def beyond_i64(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return not I64_MIN <= value <= I64_MAX
    if isinstance(value, dict):
        return any(beyond_i64(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(beyond_i64(item) for item in value)
    return False


def edge_calls() -> list[dict]:
    """Integers at and past the i64 bounds, where Rust refuses what Python computes exactly."""
    big, small = I64_MAX, I64_MIN
    calls = []
    for modifier in (big, small, big - 5, small + 5):
        calls.append({"fn": "roll", "sides": 20, "count": 2, "modifier": modifier})
        for dc in (10, big, small, 0):
            calls.append({"fn": "check", "dc": dc, "modifier": modifier, "advantage": False, "disadvantage": False})
        calls.append({"fn": "check", "dc": 10, "modifier": modifier, "advantage": True, "disadvantage": False})
        calls.append({"fn": "explode", "sides": 6, "count": 2, "modifier": modifier, "explode_on": None, "max_explosions": 3})
        calls.append({"fn": "count_successes", "sides": 10, "count": 5, "threshold": 6, "modifier": modifier})
        calls.append({"fn": "hunger_roll", "pool": 6, "hunger": 2, "sides": 10, "threshold": 6, "modifier": modifier})
    for dc in (big, small):
        calls.append({"fn": "check", "dc": dc, "modifier": 0, "advantage": False, "disadvantage": True})
    for value in (big, small):
        calls += [
            {"fn": "roll", "sides": value, "count": 1, "modifier": 0},
            {"fn": "roll", "sides": 6, "count": value, "modifier": 0},
            {"fn": "explode", "sides": value, "count": 1, "modifier": 0, "explode_on": None, "max_explosions": 1},
            {"fn": "explode", "sides": 6, "count": 1, "modifier": 0, "explode_on": value, "max_explosions": 1},
            {"fn": "explode", "sides": 6, "count": 1, "modifier": 0, "explode_on": None, "max_explosions": small},
            {"fn": "count_successes", "sides": 10, "count": 5, "threshold": value, "modifier": 0},
            {"fn": "hunger_roll", "pool": 5, "hunger": value, "sides": 10, "threshold": 6, "modifier": 0},
            {"fn": "hunger_roll", "pool": 5, "hunger": 2, "sides": value, "threshold": 6, "modifier": 0},
            {"fn": "hunger_roll", "pool": 5, "hunger": 2, "sides": 10, "threshold": value, "modifier": 0},
        ]
    for results in ([big, big], [small, small], [big, 1], [small, 1], [big, small], [1, big]):
        for predicate in ("ones", "failures", "all"):
            prior = {"sides": 6, "count": len(results), "results": results, "total": 0, "modifier": 0}
            calls.append({"fn": "reroll", "previous": prior, "predicate": predicate, "modifier": big, "threshold": 2})
    for weights in ([big, big], [big, 1], [big, big, big]):
        for roll_total in (None, big, small):
            items = [{"weight": weight} for weight in weights]
            calls.append({"fn": "weighted_pick", "items": items, "field": "weight", "roll_total": roll_total})
    return calls


def edge_outcome(call: dict) -> dict:
    """The call's outcome and whether it, or a sum it computes on the way, leaves i64."""
    result = run(call)
    refuse = beyond_i64(result.get("ok"))
    payload = result.get("ok")
    if isinstance(payload, dict):
        if call["fn"] == "check":
            roll = payload["roll_result"]
            refuse = refuse or beyond_i64(roll["total"] + roll["modifier"])
        if call["fn"] in ("count_successes", "hunger_roll"):
            faces = payload.get("results") or payload["regular_results"] + payload["hunger_results"]
            raw = sum(1 for face in faces if face >= call["threshold"]) + call["modifier"]
            refuse = refuse or beyond_i64(raw)
        if call["fn"] == "reroll":
            refuse = refuse or beyond_i64(sum(payload["results"]))
    return {"call": call, "result": result, "refuse": refuse}


class Scripted(random.Random):
    """The shape of `agent/_ruleset_test_runner.py`'s ScenarioDice: faces from a list."""

    def __init__(self, faces: list[int]) -> None:
        super().__init__(0)
        self.faces = faces
        self.position = 0
        self.calls: list[list[int]] = []

    def randint(self, a: int, b: int) -> int:
        self.calls.append([a, b])
        face = self.faces[self.position % len(self.faces)]
        self.position += 1
        return a + (face - 1) % (b - a + 1)


def context_cases() -> dict:
    """The context-variable behaviour of seeded_rolls, set_seed and use_roll_source."""
    out: dict = {}
    rolls = []
    with dice.seeded_rolls(11):
        rolls.append(dice.d20().total)
        with dice.seeded_rolls(22):
            rolls.append(dice.d20().total)
            with dice.seeded_rolls(None):
                rolls.append(dice.d20().total)
        rolls.append(dice.d20().total)
        dice.set_seed(33)
        rolls.append(dice.d20().total)
    out["nested"] = rolls

    dice.set_seed(5)
    default = [dice.d20().total]
    with dice.seeded_rolls(6):
        default.append(dice.d20().total)
    default.append(dice.d20().total)
    out["default_after_tool"] = {"rolls": default, "seed": dice.get_seed()}

    source = Scripted([3, 18, 1, 20, 7])
    with dice.use_roll_source(source):
        scripted = [dice.d20().total]
        with dice.seeded_rolls(99):
            scripted.append(dice.roll(6, 3).to_dict())
            scripted.append(dice.advantage().to_dict())
        scripted.append(dice.weighted_pick([{"weight": 2}, {"weight": 5}]))
    out["scripted"] = {"faces": source.faces, "results": scripted, "calls": source.calls}
    return out


def queue_cases() -> list[dict]:
    cases = []
    for seed in (0, 42, -7, 2**40):
        for size in (1, 3, 12):
            queue = RNGQueue(size=size, seed=seed)
            first = queue.to_dict()
            consumed = [queue.consume().to_dict() for _ in range(size + 1)]
            block = queue.to_context_block()
            remaining = queue.remaining
            queue.regenerate()
            again = queue.to_dict()
            queue.regenerate(seed + 1)
            reseeded = queue.to_dict()
            cases.append({"seed": seed, "size": size, "first": first, "consumed": consumed, "block": block,
                          "remaining": remaining, "again": again, "reseeded": reseeded})
    errors = []
    for size in (0, -2):
        try:
            RNGQueue(size=size, seed=1)
        except ValueError as error:
            errors.append({"size": size, "message": str(error)})
    return cases, errors


def expression_corpus() -> list[str]:
    fixed = [
        "d20", "D20", "2d6", "d20 + 5", "d20+5", "1d8+3", " 3d4 - 2 ", "d20 - -5", "d20-5", "4d6 + 1d4",
        "d", "2d", "xd6", "d6+x", "10", "", " ", "+", " - ", "+3", "-3", "d20+", "d20 +  ", "1000d1000",
        "1001d6", "d1001", "0d0", "-3d-2", "d1", "1d1", "2d6 - 1", "2d6 -1", "2d6 + -1", "d20+1_0",
        "d２０", "２d６", "d20 + 5", "　d8　", "\x1cd6", "d6\x1c", "Dİ", "İd6",
        "dd6", "d6d6", "2d6d6", "3 d 6", "d 6", "d20 + 5 + 2", "d20 - 2 - 1", "abc", "5 + d20", "+d20",
        "d00", "07d07", "d" + "9" * 30, "9" * 30 + "d6", "d20 + " + "9" * 30, "d" + "1" * 4301,
        "d20 + " + "1" * 4301, "1" * 4301, "d20 + 'x'", "d\U0001f3b2", "K", "ΣdΣ",
        "1۵d6", "d6 + ٣", "d20 + 5\n", "\td12\t", "d12 +\t3", "d4 - 1 + 2",
        # The i64 bounds in 19 and 20 digits: compared counts and sides, stored modifiers.
        "d20 + 9223372036854775807", "d20 + 9223372036854775808", "d20 - 9223372036854775808",
        "d20 - 9223372036854775809", "d20 + -9223372036854775808", "d20 + -9223372036854775809",
        "d20 - -9223372036854775808", "d9223372036854775807", "d9223372036854775808",
        "d99999999999999999999", "99999999999999999999d6", "-99999999999999999999d6",
        "9223372036854775807d9223372036854775807", "9223372036854775807", "9223372036854775808",
        "-9223372036854775808", "9223372036854775807 + 1", "-9223372036854775808 + -1",
        "9223372036854775806 + 1", "2d2 + 9223372036854775806", "1d2 + 9223372036854775806",
        "d20 + 1111111111111111111", "d20 + 11111111111111111111", "d20 - 1111111111111111111",
        "９" * 19 + "d6", "d6 + " + "９" * 19, "1d6 + 9_223_372_036_854_775_807",
    ]
    rng = random.Random(9001)
    alphabet = ["d", "D", "2", "0", "6", "+", "-", " ", "1", "x", "_", "６", "\t", "\x1c", "İ", " ", " - ", "9"]
    generated = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9))) for _ in range(3000)]
    return fixed + generated


def expression_cases() -> tuple[list[dict], list[dict]]:
    parsed = []
    initiative = []
    for index, text in enumerate(expression_corpus()):
        parts = _parse_dice_expr(text)
        parsed.append({"text": text, "parts": {key: str(value) for key, value in parts.items()}})
        seed = int.from_bytes(hashlib.sha256(f"initiative-{index}".encode()).digest()[:8], "big")
        with dice.seeded_rolls(seed):
            try:
                total, roll = _roll_initiative_expr(text, None)
                result = {"total": str(total), "roll": roll}
            except (ValueError, TypeError) as error:
                result = {"error": type(error).__name__, "message": str(error)}
        initiative.append({"text": text, "seed": str(seed), "result": result})
    return parsed, initiative


def lower_cases() -> dict:
    """`str.lower` for every character Unicode 15 lowercases, and final-sigma contexts."""
    chars = []
    for code in range(sys.maxunicode + 1):
        if 0xD800 <= code <= 0xDFFF:
            continue
        char = chr(code)
        if char.lower() != char:
            chars.append([code, char.lower()])
    sigma = ["ΣΑΣ", "ΑΣ Β", "Σ", "ΑΣ", "ΑΣ.", "Α.Σ", "ΑΣ'Β", "ΑΣ́", "İ", "ΑΣ1", "1Σ", "ΑΣΑ Σ", "D20 + İ", "2D6ΣΣ"]
    rng = random.Random(31337)
    pool = ["A", "d", "D", "Σ", "Α", " ", "'", ".", "́", "İ", "K", "ẞ", "Ǆ", "ǅ", "5", "𐐀"]
    texts = sigma + ["".join(rng.choice(pool) for _ in range(rng.randint(0, 8))) for _ in range(1500)]
    return {"chars": chars, "texts": [[text, text.lower()] for text in texts]}


def main() -> int:
    script = call_script()
    seeds = [0, 1, 42, 2**63 - 1, 2**64 - 1, 123456789]
    seeds += [int.from_bytes(hashlib.sha256(f"tool-{n}".encode()).digest()[:8], "big") for n in range(6)]
    runs = []
    for seed in seeds:
        with dice.seeded_rolls(seed):
            runs.append({"seed": str(seed), "results": [run(call) for call in script]})
    queues, queue_errors = queue_cases()
    edges = []
    for index, call in enumerate(edge_calls()):
        with dice.seeded_rolls(7000 + index):
            edges.append(edge_outcome(call))
    parsed, initiative = expression_cases()
    document = {
        "python": sys.version.split()[0],
        "script": script,
        "runs": runs,
        "context": context_cases(),
        "queues": queues,
        "queue_errors": queue_errors,
        "edges": edges,
        "parse_dice_expr": parsed,
        "initiative": initiative,
        "lower": lower_cases(),
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True) + "\n").encode("ascii"))
    print(f"{len(runs)} seeds x {len(script)} calls, {len(parsed)} expressions -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
