"""A paid self mask retains native choices, consumed material, lifetime and concentration."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts import srd_contingency_child_casting as child
from scripts import srd_spell_detection_masks as masks
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
        "FC14_CONTINGENCY_MASK_RUNTIME",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-mask-children-frozen-v1/excluded/selected-runtime.yaml",
    )
)




def test_changed_native_type_consumes_with_no_effect_before_strict_mask_selection():
    for actual_type, allowed in (("humanoid", True), ("fiend", False), (None, False)):
        context = ExecutionContext(
            params={"target_state": {"creature_type": actual_type}},
            vars={
                "_target_state_id": "hero",
                "_contingent_effect_eligible": True,
                "_contingency_child": {
                    "spell": "arcanists_magic_aura",
                    "mask_creature_type": "fiend",
                },
                "_contingent_self_scene": {"metadata": {"participant_ref": "hero"}},
            },
        )
        execute_effects(child._mask_eligibility(), context)
        assert context.vars["_contingent_effect_eligible"] is allowed
        if not allowed:
            assert "mask" in context.vars["_contingent_no_effect_reason"]


@pytest.fixture(scope="module")
def actual_archive():
    assert (
        hashlib.sha256(PROOF.read_bytes()).hexdigest()
        == "713f53fc7f9e758dbd0902fb4adc6f62b22a22f016cb384257137cbb7164f787"
    )
    original = combined.PROOF
    combined.PROOF = PROOF
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()
