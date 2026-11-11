"""Create a separate demo copy whose first upper-body shot clears its low wall.

The input remains frozen. Assets are copied locally, never added to the public source.
This corrects one representative fixture interaction, not the published reel or game rules.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from sylvia_gm.spatial import SpatialEngine  # noqa: E402


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def replace_once(path: Path, original: str, replacement: str) -> None:
    before = path.read_bytes()
    old = original.encode("utf-8")
    if before.count(old) != 1:
        raise ValueError(f"Expected exactly one retained fixture fragment in {path.name}")
    path.write_bytes(before.replace(old, replacement.encode("utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-source", required=True, type=Path)
    parser.add_argument("--td-package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source, output = args.demo_source.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError("The output must be a new directory outside the frozen input demo")
    input_hashes = hashes(source)
    if input_hashes.get("fixtures.js") != (
        "83c2c1a7aadb7b36ef027d8e12e22287b952c4b7379ae82a9ee6cc7a94c85e5e"
    ):
        raise ValueError("The input does not match the retained demo's verified fixture")
    with zipfile.ZipFile(args.td_package) as archive:
        state = json.loads(
            archive.read("packages/pneuma-io.trigger-discipline/conformance/scenario.json")
        )["initial_state"]
    scene = state["pneuma-io.trigger-discipline.state"]["spatial_scene"]
    wall = next(g for g in scene["geometry"] if g["id"] == "partial-cover")
    if wall["volume"]["max"][2] != 1:
        raise ValueError("This correction is for the retained demo's one-metre wall")
    ray = (
        SpatialEngine(scene)
        .query(
            {
                "kind": "ray",
                "map_id": scene["map"]["id"],
                "origin": [2.92, 2.5, 1.3],
                "destination": [9.5, 2.5, 1.2],
                "include": ["geometry"],
                "max_intersections": 64,
            }
        )
        .as_payload()
    )
    if ray["intersections"] or ray["truncated"]:
        raise ValueError("The retained upper-body shot must have a verified clear geometry path")

    output.mkdir(parents=True)
    target = output / "demo"
    shutil.copytree(source, target)
    replace_once(
        target / "fixtures.js",
        "contacts: ['cover', 'penetration', 'character'], damage: 4, note: '7 damage − MR 3 cover'",
        "contacts: ['character'], damage: 7, note: 'Clear upper-body shot above the low wall'",
    )
    replace_once(
        target / "fixtures.js",
        "label: 'Destructible cover · MR 3'",
        "label: 'Low wall · MR 3'",
    )
    # Opening the candidate must not restore the frozen donor's browser fixture state.
    replace_once(
        target / "app.js",
        "const KEY = 'sylvia-mockup-v001:';",
        "const KEY = 'sylvia-td-cover-corrected-v1:';",
    )
    after = hashes(target)
    changed = sorted(name for name in input_hashes if input_hashes[name] != after[name])
    if changed != ["app.js", "fixtures.js"] or hashes(source) != input_hashes:
        raise RuntimeError("The correction changed unexpected bytes or modified its input")
    receipt = {
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_demo": str(source),
        "output_demo": str(target),
        "package_sha256": hashlib.sha256(args.td_package.read_bytes()).hexdigest(),
        "changed_files": changed,
        "input_unchanged": True,
        "assets_and_other_modules_unchanged": True,
        "input_hashes": input_hashes,
        "output_hashes": after,
        "clear_geometry_ray": ray,
        "first_shot": {
            "contacts": ["character"],
            "representative_damage": 7,
            "material_deduction_removed": True,
        },
        "limits": (
            "Fixture-only candidate. No production rules, published video or public asset changed. "
            "Damage remains representative. Full gameplay/native and other demo actions are not "
            "qualified by this correction; fully concealed through-material firing remains open."
        ),
    }
    (output / "correction.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: receipt[k] for k in ("output_demo", "changed_files", "input_unchanged", "limits")},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
