"""Retained recovery reuses native effects and spends its consumed diamond only at setup."""

import ast
import hashlib
import importlib
import os
import sys
import types
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts import srd_contingency_child_casting as child
from scripts.srd_control_behaviors import _walk
from scripts.srd_timed_actor_defenses import _load_runtime

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
        "FC14_CONTINGENCY_RECOVERY_RUNTIME",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-recovery-children-frozen-v1/excluded/selected-runtime.yaml",
    )
)
PROOF_SHA = "33f3d9ffa1ecf4f8ec5f0b6be5519c6460674f39cf535f8a9a8af0e4fbe5d001"


def _native_curses():
    """Read the actual current native owner; its public source validation remains intact."""
    package = types.ModuleType("fc14_native_recovery_proof")
    package.__path__ = [str(native.CURRENT / "scripts")]
    sys.modules.setdefault(package.__name__, package)
    return importlib.import_module(package.__name__ + ".srd_native_undead_curses")


def _actual_native_recovery_suffix(text):
    """Use the unchanged native recovery suffix without rebuilding unrelated death owners."""
    owner = _native_curses()
    source = Path(owner.__file__)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "04843839e0fc717dc1ffd2a651dafe2802d3106d1218b5fdca607c54f296de9b"
    )
    owner._validate_sources()
    function = next(
        n
        for n in ast.parse(source.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "extend_runtime"
    )
    start = next(
        i
        for i, n in enumerate(function.body)
        if isinstance(n, ast.For)
        and isinstance(n.iter, ast.Tuple)
        and ast.literal_eval(n.iter)
        == ("remove_srd_recovery_curses", "remove_srd_one_recovery_curse")
    )
    assert isinstance(function.body[-1], ast.Return)
    statements = function.body[start:-1]
    namespace = {
        "tools": owner._load_runtime(text)["tools"],
        "deepcopy": deepcopy,
        "_set": owner._set,
        "_call": owner._call,
        "changed": {
            "read_srd_native_mummy_rot": owner._native_source_tools()["read_srd_native_mummy_rot"]
        },
    }
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), "exec"), namespace)
    return owner._replace_tools(text, namespace["changed"])




def test_stale_recovery_selection_has_no_effect_before_native_strict_selection():
    source = {
        "installed": True,
        "control_profile": "curse",
        "sequence": 7,
        "target_state_id": "hero",
    }
    child_record = {
        "effect_choice": "curse",
        "recovery_source_id": "curse_1",
        "recovery_source_sequence": 7,
        "recovery_item": "",
    }
    for current, allowed in (
        (source, True),
        (dict(source, sequence=8), False),
        (dict(source, installed=False), False),
    ):
        context = ExecutionContext(
            params={
                "target_state": {"timed_condition_source_ids": ["curse_1"]},
                "lasting_state": {"timed_condition_sources": {"curse_1": current}},
            },
            vars={
                "_target_state_id": "hero",
                "_contingency_child": child_record,
                "_contingent_effect_eligible": True,
            },
        )
        execute_effects(child._recovery_selection_eligibility(), context)
        assert context.vars["_contingent_effect_eligible"] is allowed
    assert not any("require" in n for n in _walk(child._recovery_selection_eligibility()))






@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == PROOF_SHA
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
