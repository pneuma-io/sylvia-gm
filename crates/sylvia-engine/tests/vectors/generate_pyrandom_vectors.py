"""Generate reference vectors for sylvia_engine::pyrandom from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-engine/tests/vectors/generate_pyrandom_vectors.py

(`.venv/bin/python` on Linux.) The output is deterministic. It is checked in, so the Rust
tests need no Python. This script is reference tooling for the port and is retired with the
Python source.

For each seed the same script of operations runs on a fresh `random.Random(seed)` and every
result is recorded, errors included. Integer seeds are written in hexadecimal and results as
decimal strings, because some exceed 64 bits. `random()` results are written as `repr`.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from sylvia_gm.domain.package_library import canonical_json_bytes

OUTPUT = Path(__file__).with_name("pyrandom_vectors.json")

RANDINT_RANGES = [
    (1, 2), (1, 3), (1, 4), (1, 6), (1, 8), (1, 10), (1, 12), (1, 20), (1, 100), (1, 1000),
    (0, 0), (0, 1), (5, 5), (-5, 5), (-1000, -1), (0, 2**31 - 1), (1, 2**31), (0, 2**32 - 1),
    (1, 2**32), (1, 2**32 + 1), (0, 2**53), (1, 2**63), (-(2**63), 2**63 - 1), (1, 7), (1, 17),
    (1, 33), (1, 65), (1, 129), (0, 2**33), (0, 2**64 - 1), (1, 2**64 - 1),
]
EMPTY_RANDINT = [(1, 0), (5, -5), (0, -1), (2**40, 2**39)]
RANDRANGE = [  # (start, stop, step); step None means the two-argument form
    (0, 10, None), (3, 4, None), (-7, 7, None), (0, 10, 2), (1, 10, 3), (10, 0, -1), (10, 0, -3),
    (-10, 10, 7), (0, 2**40, 2**20), (5, 5, None), (5, 4, None), (0, 10, 0), (0, 10, -1),
    (10, 0, 1), (10, 0, 2), (0, 1, 5), (100, 99, -1), (7, -8, -5),
]
RANDRANGE_ONE = [1, 2, 6, 20, 2**32, 2**32 + 3, 0, -4]
GETRANDBITS = [0, 1, 2, 5, 8, 16, 31, 32, 33, 40, 63, 64, 65, 96, 100, 127, 128]
CHOICE_LENGTHS = [1, 2, 3, 5, 7, 16, 100, 0]
SHUFFLE_LENGTHS = [0, 1, 2, 3, 10, 52]


def operations() -> list[dict]:
    """A fixed, shuffled script mixing every operation and argument shape."""
    script = []
    for a, b in RANDINT_RANGES:
        script += [{"op": "randint", "a": a, "b": b}] * 6
    for a, b in EMPTY_RANDINT:
        script.append({"op": "randint", "a": a, "b": b})
    for start, stop, step in RANDRANGE:
        script += [{"op": "randrange", "start": start, "stop": stop, "step": step}] * 3
    for stop in RANDRANGE_ONE:
        script += [{"op": "randrange_to", "stop": stop}] * 2
    for k in GETRANDBITS:
        script += [{"op": "getrandbits", "k": k}] * 3
    for length in CHOICE_LENGTHS:
        script += [{"op": "choice", "len": length}] * 3
    for length in SHUFFLE_LENGTHS:
        script.append({"op": "shuffle", "len": length})
    script += [{"op": "random"}] * 12
    random.Random(20261121).shuffle(script)
    # A long tail of ordinary dice crosses several MT19937 regenerations.
    script += [{"op": "randint", "a": 1, "b": 20}] * 700
    script += [{"op": "getrandbits", "k": 32}] * 700
    return script


def run(rng: random.Random, op: dict) -> object:
    kind = op["op"]
    try:
        if kind == "randint":
            return str(rng.randint(op["a"], op["b"]))
        if kind == "randrange":
            if op["step"] is None:
                return str(rng.randrange(op["start"], op["stop"]))
            return str(rng.randrange(op["start"], op["stop"], op["step"]))
        if kind == "randrange_to":
            return str(rng.randrange(op["stop"]))
        if kind == "getrandbits":
            return str(rng.getrandbits(op["k"]))
        if kind == "choice":
            return str(rng.choice(range(op["len"])))
        if kind == "shuffle":
            values = list(range(op["len"]))
            rng.shuffle(values)
            return values
        if kind == "random":
            return repr(rng.random())
    except (ValueError, IndexError) as error:
        return {"error": type(error).__name__, "message": str(error)}
    raise AssertionError(kind)


def encode_op(op: dict) -> dict:
    """Integers as decimal strings: several are beyond 64 bits."""
    return {key: str(value) if isinstance(value, int) else value for key, value in op.items()}


def int_seeds() -> list[int]:
    rng = random.Random(4242)
    seeds = [
        0, 1, 2, 3, 42, 1234, 2**31 - 1, 2**31, 2**32 - 1, 2**32, 2**32 + 1, 2**48 + 7,
        2**63 - 1, 2**63, 2**64 - 1, -1, -42, -(2**32), -(2**63), -(2**63) + 1,
        2**64, 2**64 + 1, 2**96 - 1, 2**100 + 12345, 2**128 - 1, 3**200, -(5**90),
    ]
    seeds += [rng.getrandbits(64) for _ in range(12)]
    seeds += [rng.getrandbits(32) for _ in range(4)]
    seeds += [rng.getrandbits(rng.choice((65, 200, 640, 20000))) for _ in range(4)]
    return seeds


def seed_bases() -> list[dict]:
    """Seed bases in the shape `application/package_runtime.py` builds for a tool call."""
    tools = ("roll_check", "make_attack", "cast_spell", "roll_initiative", "discard_tile")
    bases = []
    for index in range(16):
        bases.append({
            "campaign_id": f"campaign-{index:04d}",
            "turn_id": f"turn-{index * 7}",
            "action_id": None if index % 5 == 0 else f"action-{index}",
            "package_id": "srd-lite" if index % 2 else "riichi",
            "package_version": "0.3.28",
            "package_sha256": hashlib.sha256(f"package-{index}".encode()).hexdigest(),
            "tool_name": tools[index % len(tools)],
            "parameters": {
                "actor": "pc-1", "target": "wolf-2", "dc": 10 + index, "advantage": index % 3 == 0,
                "bonus": 1.5 * index, "note": "Régles ☃" if index % 4 == 0 else "plain",
                "nested": {"z": [3, 2, 1], "a": {"b": None}},
            },
        })
    return bases


def main() -> int:
    script = operations()
    seeds = []
    for seed in int_seeds():
        rng = random.Random(seed)
        hex_value = ("-" if seed < 0 else "") + format(abs(seed), "x")
        seeds.append({"kind": "int", "hex": hex_value, "results": [run(rng, op) for op in script]})
    for text in ["", "sylvia", "Régles ☃", "0"]:
        rng = random.Random(text)
        seeds.append({"kind": "str", "value": text, "results": [run(rng, op) for op in script]})
    for raw in [b"", b"\x00" * 16, bytes(range(16)), hashlib.sha256(b"x").digest()[:16]]:
        rng = random.Random(raw)
        seeds.append({"kind": "bytes", "value": raw.hex(), "results": [run(rng, op) for op in script]})

    derived = []
    for basis in seed_bases():
        # The expression `application/package_runtime.py` uses for a tool's seed.
        seed_basis = canonical_json_bytes(basis)
        seed = int.from_bytes(hashlib.sha256(seed_basis).digest()[:8], "big")
        rng = random.Random(seed)
        derived.append({
            "basis": basis,
            "canonical": seed_basis.decode("utf-8"),
            "seed": str(seed),
            "d20s": [rng.randint(1, 20) for _ in range(20)],
        })

    document = {
        "python": sys.version.split()[0],
        "script": [encode_op(op) for op in script],
        "seeds": seeds,
        "derived": derived,
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=None) + "\n").encode("ascii"))
    print(f"{len(seeds)} seeds x {len(script)} operations, {len(derived)} derived seeds -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
