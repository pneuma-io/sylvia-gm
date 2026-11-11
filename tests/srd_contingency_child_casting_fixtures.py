"""Retained family effects reuse genuine preparation, payment, and release dispatches."""

import hashlib
import os
import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts import srd_contingency_child_casting as child
from scripts import srd_detection_mask_casting as mask_casting
from scripts import srd_spell_detection_masks as masks
from scripts.srd_control_behaviors import _walk
from scripts.srd_timed_actor_defenses import _load_runtime

ROOT = Path(__file__).resolve().parents[1]
CURRENT = Path(
    os.environ.get(
        "FC14_CONTINGENCY_SOURCE_ROOT", "historical/srd-full"
    )
)
PARENT = Path(
    os.environ.get(
        "FC14_CONTINGENCY_CHILD_PARENT",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-clone-order-frozen-v1/excluded/selected-runtime.yaml",
    )
)


def _with_native_masks(text, definitions):
    """Add only this missing native family to the hash-verified historical proof ancestor."""
    names = ("arcanists_magic_aura", "nondetection", "pass_without_trace")
    assert all(definitions[name] == masks.PROFILES[name] for name in names)
    parsed = _load_runtime(text)
    missing = {
        name: definitions[name] for name in names if name not in parsed["definitions"]["spells"]
    }
    if missing:
        offset = text.index("  spells:\n", text.index("\ndefinitions:\n")) + len("  spells:\n")
        rows = yaml.safe_dump(missing, sort_keys=False)
        text = (
            text[:offset] + "".join("    " + line for line in rows.splitlines(True)) + text[offset:]
        )
        changed = {}
        for name in (
            "begin_srd_spell_cast_internal",
            "prepare_srd_contingent_child",
            "cast_spell_outside_combat",
            "cast_spell",
        ):
            tool = deepcopy(parsed["tools"][name])
            selector = next(p for p in tool["parameters"] if p["name"] == "spell")
            assert set(selector["enum_values"]) == set(parsed["definitions"]["spells"])
            selector["enum_values"] = [*parsed["definitions"]["spells"], *missing]
            changed[name] = tool
        text = child._emit_changed(text, changed)
    return mask_casting.extend_runtime(masks.extend_runtime(text))


@pytest.fixture(scope="module")
def actual():
    content = PARENT.read_bytes()
    assert hashlib.sha256(content).hexdigest() == (
        "c71f1b82ae647cbe6a4e413a6baf887c2f3c0586a60807a01d42945ac0404514"
    )
    text = PARENT.read_text()
    declarations = yaml.load(
        (CURRENT / "rulesets/srd-lite/content/spell-mechanics.yaml").read_text(),
        Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader),
    )
    definitions = {name: row["definition"] for name, row in declarations["spells"].items()}
    if "detection_mask_spell" in child.DISPATCHES:
        text = _with_native_masks(text, definitions)
    tools = {}
    for name in (
        "prepare_srd_contingent_child",
        "prepare_srd_contingency_cast",
        "install_srd_contingency_source",
        "cast_spell",
        "release_srd_contingent_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        *child.DISPATCHES.values(),
    ):
        match = re.search(
            rf"(?ms)^  {name}:[^\n]*\n.*?(?=^  [A-Za-z_][A-Za-z0-9_]*:|^definitions:|^procedures:)",
            text,
        )
        assert match, name
        tools[name] = _load_runtime("tools:\n" + match[0])["tools"][name]
    return tools, definitions
