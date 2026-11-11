"""Paid contingent concentration and consumed components use their real release owners."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests import srd_contingency_combined_public_fixtures as combined
from tests import test_srd_public_spell_casting as public_casting
from tests.test_srd_npc_spell_casting import _act

ROOT = Path(__file__).resolve().parents[1]
PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_CHILD_RUNTIME",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-child-casting-frozen-v1/excluded/selected-runtime.yaml",
    )
)


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "0d3a7d46db611b379b35c9c1331ac5ee56fee22e778581daa0277f661195ccd7"
    )
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {"change_srd_equipment"}
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


def _case(monkeypatch, spell):
    from sylvia_gm.application import package_runtime

    execute = package_runtime.execute_tool

    def checked(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("NATIVE", args[0], result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", checked)
    state, data, parameters, _ = combined._case(monkeypatch)
    spells = [*data["wizard_spellbook"], spell]
    data["wizard_spellbook"] = spells[:]
    data["spellcasting_grants"][0]["spells"] = spells[:]
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["prepared_spells"] = spells[:]
    lots = []
    for index, part in enumerate(
        combined._runtime()["definitions"]["spell_casting"][spell]["material_parts"]
    ):
        lot = {
            "id": f"chosen_component_{index}",
            "inventory_item": f"chosen_inventory_{index}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"] * 3,
            "value_cp": part["minimum_value_cp"],
        }
        hero["spell_materials"].append(lot)
        hero["inventory"].append(lot["inventory_item"])
        lots.append(lot["id"])
    base_level = combined._runtime()["definitions"]["spells"][spell]["base_level"]
    parameters.update(
        contingent_spell=spell,
        contingent_slot_resource=f"spell_slot_{base_level}",
        contingent_material_item_ids=lots,
        contingent_effect_choice="warm",
    )
    return state, data, parameters, lots, base_level
