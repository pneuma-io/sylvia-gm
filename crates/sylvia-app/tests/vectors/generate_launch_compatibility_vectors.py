"""Generate reference vectors for `sylvia_app::launch_compatibility::player_ui_surfaces`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-app/tests/vectors/generate_launch_compatibility_vectors.py

Each case is a package lock (its primary package and, optionally, a dependency) and a list of
player UI surfaces, run through the real `player_ui_surfaces`: the surfaces it returns, thawed
to JSON with their key order, or the exception's class and text. Reference tooling only;
retired with the Python source.
"""

from __future__ import annotations

import json
from pathlib import Path

from sylvia_gm.application.launch_compatibility import player_ui_surfaces
from sylvia_gm.domain.authority import (
    PackageLock,
    PackageLockEntry,
    PackageRole,
    owned_payload,
    thaw_json_value,
)

HERE = Path(__file__).parent
OUTPUT = HERE / "launch_compatibility_vectors.json"
SHA = "a" * 64
OTHER_SHA = "b" * 64

END = {"id": "end", "intent": "end_combat_encounter", "label": "End encounter"}
ATTACK = {"id": "attack", "intent": "attack", "label": "Attack"}
NO_INTENT = {"id": "look", "label": "Look"}
NUMBER_INTENT = {"id": "odd", "intent": 5}

SURFACES = [
    {"id": "combat", "kind": "panel", "actions": [ATTACK, END, NO_INTENT, NUMBER_INTENT], "title": "Combat"},
    {"actions": [], "id": "empty"},
    {"id": "only-end", "actions": [END, END]},
]


def lock(primary: str, dependency: str | None = None) -> PackageLock:
    entries = [PackageLockEntry(primary, "1.0.0", SHA, PackageRole.PRIMARY, 0, (), ())]
    if dependency is not None:
        entries.append(PackageLockEntry(dependency, "1.0.0", OTHER_SHA, PackageRole.DEPENDENCY, 1, (), ()))
    return PackageLock(tuple(entries))


CASES = [
    ("lite primary", ("sylvia.srd-lite.bg3", None), SURFACES),
    ("lite with a dependency", ("sylvia.srd-lite.bg3", "pneuma.severed-lands"), SURFACES),
    ("another primary", ("pneuma.riichi-mahjong", None), SURFACES),
    ("lite only as a dependency", ("pneuma.riichi-mahjong", "sylvia.srd-lite.bg3"), SURFACES),
    ("empty lock", None, SURFACES),
    ("no surfaces", ("sylvia.srd-lite.bg3", None), []),
    ("surface without actions", ("sylvia.srd-lite.bg3", None), [{"id": "bare"}]),
    ("action that is text", ("sylvia.srd-lite.bg3", None), [{"id": "text", "actions": ["end_combat_encounter"]}]),
    ("actions as a mapping", ("sylvia.srd-lite.bg3", None), [{"id": "map", "actions": {"intent": "attack"}}]),
    ("actions as a number", ("sylvia.srd-lite.bg3", None), [{"id": "number", "actions": 3}]),
    ("unchecked shapes without lite", ("pneuma.riichi-mahjong", None), [{"id": "bare"}]),
]


def main() -> None:
    cases = []
    for name, spec, surfaces in CASES:
        package_lock = PackageLock(()) if spec is None else lock(*spec)
        record = {
            "name": name,
            "lock": None if spec is None else {"primary": spec[0], "dependency": spec[1]},
            "surfaces": surfaces,
        }
        try:
            result = player_ui_surfaces([owned_payload(surface) for surface in surfaces], package_lock)
            record["result"] = [thaw_json_value(surface) for surface in result]
        except Exception as error:  # noqa: BLE001 - the exact Python outcome is the vector
            record["error"] = {"class": type(error).__name__, "message": str(error)}
        cases.append(record)
    OUTPUT.write_text(json.dumps({"cases": cases}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(cases)} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
