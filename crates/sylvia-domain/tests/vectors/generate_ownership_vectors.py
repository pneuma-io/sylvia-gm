"""Generate reference vectors for sylvia_domain::state_ownership from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_ownership_vectors.py

The output is deterministic (fixed seed) and checked in. This script is reference tooling for
the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.domain.state_ownership import owner_matches_package_lock  # noqa: E402

OUTPUT = Path(__file__).with_name("ownership_vectors.json")


def main() -> None:
    rng = random.Random(20261121)
    packages = ["a", "a.b", "a.b.c", "ab", "pkg", "pkg.x", "sylvia.srd-lite.bg3", ""]
    namespaces = ["a", "a.", "a.x", "a.b", "a.b.x", "a.b.c.d", "ab.x", "pkg.x.y", "pkg", "pkgx.y",
                  "sylvia.srd-lite.bg3.state", ".x", "", "x"]
    cases = []
    for _ in range(1500):
        selected = rng.sample(packages, rng.randint(0, 4))
        namespace = rng.choice(namespaces)
        owner = rng.choice([None, *packages, "other"])
        cases.append({"packages": selected, "namespace": namespace, "owner": owner,
                      "matches": owner_matches_package_lock(selected, namespace, owner)})
    OUTPUT.write_text(json.dumps({"python": sys.version.split()[0], "cases": cases}, separators=(",", ":")) + "\n",
                      encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
