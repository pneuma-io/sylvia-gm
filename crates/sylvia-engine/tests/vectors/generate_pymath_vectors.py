"""Generate `math.hypot` and `math.dist` vectors for sylvia_engine::pymath from CPython.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_pymath_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Every float is written as its IEEE 754 bits (an unsigned 64-bit integer), so the comparison is
exact and non-finite values survive JSON. The corpus: hand-picked edges (zeros, signed zeros,
subnormals, the largest finite values, infinities, NaN, values that overflow a naive sum of
squares, 3-4-5 triangles) and seeded random points of two to four coordinates drawn at many
magnitudes, including the scene-coordinate range spatial queries use.
"""

from __future__ import annotations

import json
import math
import random
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "pymath_vectors.json"


def bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


EDGES = [
    [0.0, 0.0], [-0.0, 0.0], [3.0, 4.0], [5.0, 12.0], [1.0], [], [0.0], [-2.5], [1e-310, 1e-310], [5e-324, 5e-324],
    [5e-324, 0.0, 1e-320], [2.2250738585072014e-308, 1e-310], [1.7976931348623157e308, 1.7976931348623157e308],
    [1e308, 1e308, 1e308], [1e200, 1e-200], [float("inf"), 1.0], [float("-inf"), float("nan")], [float("nan"), 1.0],
    [1.0, 1.0], [1.0, 1.0, 1.0], [0.1, 0.2, 0.3], [1e6, 1e6, 1e6], [1e6, 1e-6], [0.5, 0.5, 0.5, 0.5],
    [2.0**-1022, 2.0**-1023], [2.0**-1074, 2.0**-1073, 2.0**-1072], [2.0**1023, 2.0**1023],
]


def random_point(rng: random.Random, size: int) -> list[float]:
    scale = rng.choice([1e-300, 1e-12, 1e-6, 1e-3, 1.0, 10.0, 1e3, 1e6, 1e12, 1e150, 1e300])
    point = []
    for _ in range(size):
        kind = rng.random()
        if kind < 0.1:
            point.append(0.0)
        elif kind < 0.2:
            point.append(float(rng.randint(-1000, 1000)))
        else:
            point.append(rng.uniform(-1.0, 1.0) * scale)
    return point


def main() -> int:
    rng = random.Random(4804)
    hypot_cases, dist_cases = [], []
    for point in EDGES:
        hypot_cases.append({"input": [bits(x) for x in point], "result": bits(math.hypot(*point))})
    for first in EDGES:
        for second in (first, [1.0] * len(first), [-x for x in first]):
            dist_cases.append({"p": [bits(x) for x in first], "q": [bits(x) for x in second],
                               "result": bits(math.dist(first, second))})
    for _ in range(20000):
        size = rng.randint(2, 4)
        point = random_point(rng, size)
        hypot_cases.append({"input": [bits(x) for x in point], "result": bits(math.hypot(*point))})
        other = random_point(rng, size)
        dist_cases.append({"p": [bits(x) for x in point], "q": [bits(x) for x in other],
                           "result": bits(math.dist(point, other))})
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "hypot": hypot_cases, "dist": dist_cases},
                                   ensure_ascii=True) + "\n").encode("ascii"))
    print(f"{len(hypot_cases)} hypot cases, {len(dist_cases)} dist cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
