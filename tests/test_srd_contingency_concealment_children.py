"""Self concealment retains native effects and factual anatomy while setup owns costs."""

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
        "FC14_CONTINGENCY_CONCEALMENT_RUNTIME",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-concealment-children-frozen-v1/excluded/selected-runtime.yaml",
    )
)




def test_changed_anatomy_has_no_effect_before_native_strict_appearance_validation():
    for limb, allowed in (("humanoid", True), ("quadruped", False)):
        context = ExecutionContext(
            params={"target_state": {"form_id": None}},
            vars={
                "_target_state_id": "hero",
                "_contingent_effect_eligible": True,
                "_contingency_child": {
                    "spell": "disguise_self",
                    "appearance_specs": [
                        {
                            "subject": "hero",
                            "description": "silver cloak",
                            "height_offset_inches": 6,
                            "limb_arrangement": "humanoid",
                            "willing": True,
                        }
                    ],
                },
                "_contingent_self_scene": {
                    "height": 6,
                    "metadata": {"basic_limb_arrangement": limb},
                },
            },
        )
        execute_effects(child._appearance_eligibility(), context)
        assert context.vars["_contingent_effect_eligible"] is allowed
        if not allowed:
            assert "appearance" in context.vars["_contingent_no_effect_reason"]


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "07771143ffd50698e22abce17273f1a5504bcf657ddf07401e09b654e84609b7"
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
