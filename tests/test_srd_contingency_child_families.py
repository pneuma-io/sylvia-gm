"""Native healing, information and lasting buffs release once from their genuinely paid source."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts import srd_contingency_child_casting as child
from scripts.srd_control_behaviors import _walk
from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext
from tests import srd_contingency_child_casting_fixtures as native
from tests import srd_contingency_child_public_fixtures as prior
from tests import srd_contingency_combined_public_fixtures as combined
from tests import test_srd_public_spell_casting as public_casting
from tests.test_srd_npc_spell_casting import _act

PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_CHILD_FAMILIES_RUNTIME",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-child-families-frozen-v1/excluded/selected-runtime.yaml",
    )
)






def test_self_scene_eligibility_uses_actual_unique_scene_and_local_count():
    row = {
        "id": "body",
        "active": True,
        "metadata": {"participant_ref": "hero", "plane": "material"},
    }
    for entries, allowed in (([row], True), ([], False), ([row, deepcopy(row)], False)):
        context = ExecutionContext(
            params={
                "lasting_state": {
                    "has_magic_jar_sources": False,
                    "spatial_scene": {
                        "map": {"grid": {"unit": "ft"}},
                        "entities": entries,
                    },
                }
            },
            vars={"_target_state_id": "hero", "_contingent_effect_eligible": True},
        )
        execute_effects(child._self_scene_eligibility(), context)
        assert context.vars["_contingent_scene_matches"] == len(entries)
        assert context.vars["_contingent_effect_eligible"] is allowed


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "0a951053d87b0f2110ae3497e707a377ffffa60051fc598ec69bb5698459582d"
    )
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {"query_srd_information_spell"}
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()
