"""Late native recovery effect-group forwarding keeps genuine self recovery selections."""

import ast
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_contingency_child_casting as child
from scripts.srd_full.srd_actor_spell_modifiers import _emit
from scripts.srd_full.srd_control_behaviors import _param, _walk
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from tests import test_srd_contingency_child_casting as native
from tests import test_srd_contingency_recovery_children as recovery

ADOPTION = native.CURRENT / "scripts/srd_spell_removal_adoption.py"
PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-recovery-binding/selected-runtime.yaml")
)


def _actual_effect_group_forwarding(text):
    """Apply only the original adapter's actual parameter/forwarding statements."""
    assert hashlib.sha256(ADOPTION.read_bytes()).hexdigest() == (
        "b21333a295d2260ed85f64f9a924bf512eac54975c4a0e7d7cda2eed3d52ce42"
    )
    source = ast.parse(ADOPTION.read_text())
    owner = next(
        n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "_guards_wards_tools"
    )
    suffix = owner.body[-2:]
    assert all(isinstance(n, ast.For) for n in suffix)
    assert ast.literal_eval(suffix[0].iter) == ("cast_spell", "cast_srd_recovery")
    assert "recovery_effect" in ast.unparse(suffix[0])
    assert "cast_srd_recovery" in ast.unparse(suffix[1])
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = {}
    namespace = {
        "tools": tools,
        "changed": changed,
        "deepcopy": deepcopy,
        "_param": _param,
        "_walk": _walk,
    }
    exec(compile(ast.Module(body=suffix, type_ignores=[]), str(ADOPTION), "exec"), namespace)
    return _emit(text, changed)


def test_actual_late_recovery_adapter_binds_self_choices_and_preserves_native_suffix(monkeypatch):
    assert hashlib.sha256(PARENT.read_bytes()).hexdigest() == (
        "0dfd38a8b61952ab9cafc0a672ee9cecff5a7548f46916e1b3591356aa4f8c1b"
    )
    text = recovery._actual_native_recovery_suffix(PARENT.read_text())
    text = _actual_effect_group_forwarding(text)
    original = _load_runtime(text)["tools"]
    descriptor = next(
        p for p in original["cast_srd_recovery"]["parameters"] if p["name"] == "recovery_effect"
    )
    assert descriptor == _param("recovery_effect", required=False, default="")
    emitted = child.bind_release(text)
    final = _load_runtime(emitted)["tools"]
    retained = final["release_srd_contingent_recovery"]
    assert retained["effects"] == original["cast_srd_recovery"]["effects"]
    assert any(
        n.get("set", {}).get("field") == "target_state.native_undead_max_hp_reduction_receipts"
        for n in _walk(retained["effects"])
    )
    call = next(
        n
        for n in _walk(final[child.RELEASE]["effects"])
        if n.get("call") == ("release_srd_contingent_recovery")
    )
    assert call["params"]["recovery_effect"] == ""
    assert call["params"]["recovery_choice"] == "{_contingency_child.effect_choice}"
    assert call["params"]["recovery_item"] == "{_contingency_child.recovery_item}"
    assert call["params"]["recovery_source_id"] == "{_contingency_child.recovery_source_id}"
    assert final["cast_srd_recovery"] == original["cast_srd_recovery"]
    assert final["read_srd_native_mummy_rot"] == original["read_srd_native_mummy_rot"]
    for name in (child.RELEASE, "prepare_srd_contingent_child"):
        assert any(
            n.get("call") == "read_srd_native_mummy_rot" for n in _walk(final[name]["effects"])
        )
    assert child.bind_release(emitted) == emitted
    actual_call = next(
        n
        for n in _walk(original["cast_spell"]["effects"])
        if n.get("call") == "cast_srd_recovery" and "target_state" in n["params"]
    )
    with monkeypatch.context() as patch:
        patch.setattr(child, "SELECTED", dict(child.SELECTED, dispel_magic="recovery"))
        with pytest.raises(ValueError, match="genuine retained effect-group"):
            child._dispatch(actual_call)
